import time
import random
import sys
import logging
import sqlite3
import socket
import os
from datetime import datetime, timezone, timedelta

import paho.mqtt.client as mqtt
import smbus2
import board
import adafruit_dht
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/admin/iot_project/logs/mqtt_publisher.log'),
        logging.StreamHandler(sys.stdout)  # 便于 systemd/journalctl 捕获
    ]
)
logger = logging.getLogger(__name__)

# ==================== 配置常量 ====================
# InfluxDB
INFLUXDB_URL = "http://DESKTOP-P5EOAMG.local:8086"
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "your-token-here")
INFLUXDB_ORG = "my_org"
INFLUXDB_BUCKET = "iot_data"
INFLUXDB_TIMEOUT = 10_000  # 毫秒

# MQTT
REMOTE_BROKER = "DESKTOP-P5EOAMG.local" #电脑ip或mDNS
REMOTE_PORT = 18883 #docker 1883在电脑的映射
LOCAL_BROKER = "localhost"
LOCAL_PORT = 1883
KEEPALIVE = 60

# 数据库
DB_PATH = "/home/admin/iot_project/data_cache.db"

# 其他
RESEND_INTERVAL = 30  # 秒
CLEAN_INTERVAL = 86400  # 24小时
SLEEP_INTERVAL = 5  # 主循环睡眠秒数
INFLUX_RETRY_ATTEMPTS = 3

# ==================== 全局变量 ====================
influx_client = None
write_api = None
local_client = None   # 连接树莓派本地 Broker，订阅 ESP32 数据
remote_client = None  # 连接电脑 Docker Broker，发布转发数据
db_conn = None  # 全局连接
pending_data = {}

# ==================== 传感器初始化 ====================
bus = smbus2.SMBus(1)
BH1750_ADDR = 0x23
CMD_MEASURE = 0x10

board_dth22 = board.D4
dht = adafruit_dht.DHT22(board_dth22, use_pulseio=False)

# ==================== 传感器读取函数 ====================
'''

def read_lux():
    """读取光照强度 (单位: lux)"""
    try:
        bus.write_byte(BH1750_ADDR, CMD_MEASURE)
        time.sleep(0.2)
        data = bus.read_i2c_block_data(BH1750_ADDR, 0x00, 2)
        lux_raw = (data[0] << 8) | data[1]
        lux = lux_raw / 1.2
        return round(lux, 2)
    except Exception as e:
        logger.error(f"光照传感器读取出错: {e}")
        return None

def read_dth22(max_retries=10):
    """读取 DHT22，带重试机制，返回 (温度, 湿度) 或 (None, None)"""
    for _ in range(max_retries):
        try:
            temp = dht.temperature
            humid = dht.humidity
            if temp is not None and humid is not None:
                return temp, humid
        except RuntimeError:
            pass
        time.sleep(0.2)
    return None, None

'''


# ==================== 网络检测 ====================
def is_network_available(host="8.8.8.8", port=53, timeout=3):
    """检测网络是否可达（使用 DNS 服务器）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
        return True
    except socket.error:
        return False


# ==================== InfluxDB 管理 ====================
def init_influx():
    """初始化 InfluxDB 客户端，返回成功状态"""
    global influx_client, write_api
    try:
        if influx_client:
            influx_client.close()
        influx_client = InfluxDBClient(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG,
            timeout=INFLUXDB_TIMEOUT
        )
        write_api = influx_client.write_api(write_options=SYNCHRONOUS)
        logger.info("InfluxDB 客户端初始化成功")
        return True
    except Exception as e:
        logger.error(f"InfluxDB 初始化失败: {e}")
        return False


def write_to_influx(temp, humid, lux, timestamp):
    """
    写入 InfluxDB，带重试和重建连接机制
    返回 True 表示写入成功，False 表示最终失败
    """
    global influx_client, write_api
    for attempt in range(INFLUX_RETRY_ATTEMPTS):
        try:
            point = Point("sensor_data") \
                .tag("device", "rpi4b") \
                .field("temperature", float(temp)) \
                .field("humidity", float(humid)) \
                .field("lux", float(lux)) \
                .time(timestamp)
            write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
            return True
        except Exception as e:
            logger.warning(f"InfluxDB 写入失败 (尝试 {attempt + 1}/{INFLUX_RETRY_ATTEMPTS}): {e}")
            if attempt < INFLUX_RETRY_ATTEMPTS - 1:
                time.sleep(2)
                if not init_influx():
                    logger.error("重新初始化 InfluxDB 失败，等待下次尝试")
            else:
                logger.error("写入 InfluxDB 最终失败")
                return False
    return False


# ==================== MQTT 管理 ====================
def connect_mqtt(mqtt_client,BROKER, PORT, KEEPALIVE):
    """建立或重建 MQTT 连接，返回连接成功状态"""

    try:
        if mqtt_client.is_connected():
            return True
        if mqtt_client:
            mqtt_client.loop_stop()  # 停止旧循环
        mqtt_client.connect(BROKER, PORT, KEEPALIVE)
        mqtt_client.loop_start()
        logger.info("MQTT 已重新连接")
        return True
    except Exception as e:
        logger.error(f"MQTT 连接失败: {e}")
        return False


# MQTT 订阅
def on_message_local(client, userdata, msg):
    """处理来自 ESP32 的 MQTT 消息（按帧号配对）"""
    global pending_data
    try:
        # 解析 payload 为 "数值,帧号"
        payload_str = msg.payload.decode()
        parts = payload_str.split(',')
        if len(parts) != 2:
            logger.warning(f"无效数据格式: {payload_str}")
            return

        value = float(parts[0])
        seq = int(parts[1])
        topic = msg.topic

        logger.info(f"收到 ESP32 [{topic}]: {value}, seq={seq}")

        # 如果是新帧号，初始化
        if seq not in pending_data:
            pending_data[seq] = {
                'temp': None,
                'humid': None,
                'lux': None,
                '_time': time.time()  # 记录开始时间
            }
        else:
            # 更新已有帧的时间戳（防止超时丢弃）
            pending_data[seq]['_time'] = time.time()

        # 填入数据
        if topic == "sensor/temperature":
            pending_data[seq]['temp'] = value
        elif topic == "sensor/humidity":
            pending_data[seq]['humid'] = value
        elif topic == "sensor/lux":
            pending_data[seq]['lux'] = value
        else:
            return

        # 检查是否凑齐三条数据
        d = pending_data[seq]
        if d['temp'] is not None and d['humid'] is not None and d['lux'] is not None:
            temp = d['temp']
            humid = d['humid']
            lux = d['lux']
            # 删除已处理的帧
            del pending_data[seq]

            # 保存并发送
            save_and_sent(temp, humid, lux)

    except ValueError as e:
        logger.error(f"数据解析错误: {e}, 原始数据: {msg.payload}")
    except Exception as e:
        logger.error(f"处理 MQTT 消息出错: {e}")


# ==================== 数据库操作 ====================
def get_db_connection():
    """获取全局数据库连接，若未创建则创建"""
    global db_conn
    if db_conn is None:
        db_conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        db_conn.execute("PRAGMA journal_mode=WAL")
        db_conn.execute("PRAGMA synchronous=NORMAL")
        # 初始化表（如果未创建）
        cursor = db_conn.cursor()
        cursor.execute("""
               create table if not exists sensor_data(
                  id integer primary key autoincrement,
                  timestamp text not null,
                  temperature real,
                  humidity real,
                  lux real,
                  sent integer default 0
               )   
                """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sent ON sensor_data(sent)")
        db_conn.commit()
    return db_conn


def init_db():
    """初始化（只需调用一次，确保连接存在）"""
    get_db_connection()
    logger.info("数据库初始化完成（全局连接已建立）")


def cache_data(temp, humid, lux, max_retries=3, retry_delay=1):
    """缓存数据，带重试和自动修复"""
    global db_conn
    conn = get_db_connection()
    for attempt in range(max_retries):
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    INSERT INTO sensor_data(timestamp, temperature, humidity, lux, sent)
                    VALUES(?, ?, ?, ?, 0)
                """, (datetime.now(timezone.utc).isoformat(), temp, humid, lux))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.OperationalError as e:
            if "unable to open database file" in str(e) and attempt < max_retries - 1:
                logger.warning(f"数据库打开失败，{retry_delay}秒后重试 ({attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                # 尝试重新建立连接
                db_conn.close()
                db_conn = None
                conn = get_db_connection()
                continue
            logger.error(f"数据库写入失败: {e}")
            return None
        except Exception as e:
            logger.error(f"未知数据库错误: {e}")
            return None
    return None


def get_unsent_data(limit=100):
    """获取最多 limit 条未发送的数据"""
    conn = get_db_connection()
    try:

        cursor = conn.cursor()
        cursor.execute("""
                SELECT id, timestamp, temperature, humidity, lux
                FROM sensor_data
                WHERE sent=0
                ORDER BY id
                LIMIT ?
            """, (limit,))
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"获取未发送数据失败: {e}")
        return []


def get_unsent_count():
    """获取未发送数据总数"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sensor_data WHERE sent=0")
        return cursor.fetchone()[0]
    except sqlite3.Error as e:
        logger.error(f"获取未发送数量失败: {e}")
        return -1


def mark_as_sent(ids):
    """将指定 ID 的数据标记为已发送"""
    if not ids:
        return
    conn = get_db_connection()
    try:

        cursor = conn.cursor()
        placeholders = ','.join('?' * len(ids))
        sql = f"UPDATE sensor_data SET sent=1 WHERE id IN ({placeholders})"
        cursor.execute(sql, ids)
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"标记已发送失败: {e}")


def clean_old_sent_data(days_to_keep=1):
    """删除指定天数之前且已标记为发送的数据"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sensor_data WHERE sent=1 AND timestamp < ?", (cutoff,))
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            logger.info(f"已清理 {deleted} 条 {days_to_keep} 天前的已发送历史数据")
        return deleted
    except sqlite3.Error as e:
        logger.error(f"清理旧数据失败: {e}")
        return 0


# ==================== 补发机制 ====================
def resend_cached_data(client, limit=100):
    """补发未发送的数据，返回成功补发条数"""
    unsent = get_unsent_data(limit=limit)
    if not unsent:
        return 0

    logger.info(f"检测到 {len(unsent)} 条未发送数据，开始补发...")
    success_ids = []
    for row in unsent:
        record_id, ts_str, temp, humid, lux = row

        # 解析原始时间戳
        try:
            original_time = datetime.fromisoformat(ts_str)
        except ValueError:
            logger.warning(f"时间戳格式错误: {ts_str}，使用当前时间")
            original_time = datetime.now(timezone.utc)

        # 发送 MQTT
        temp_ok = client.publish("sensor/temperature", payload=str(temp)).rc == mqtt.MQTT_ERR_SUCCESS
        humid_ok = client.publish("sensor/humidity", payload=str(humid)).rc == mqtt.MQTT_ERR_SUCCESS
        lux_ok = client.publish("sensor/lux", payload=str(lux)).rc == mqtt.MQTT_ERR_SUCCESS

        if not (temp_ok and humid_ok and lux_ok):
            logger.warning(f"MQTT 补发失败，当前 ID {record_id}，停止补发")
            break

        # 写入 InfluxDB
        if not write_to_influx(temp, humid, lux, original_time):
            logger.warning(f"InfluxDB 补发失败，当前 ID {record_id}，停止补发")
            break

        success_ids.append(record_id)
        time.sleep(0.05)

    if success_ids:
        mark_as_sent(success_ids)
        logger.info(f"成功补发并标记了 {len(success_ids)} 条历史数据")
    else:
        logger.info("本次没有成功补发任何数据")

    return len(success_ids)

# ==================== 帧管理 ====================
def clean_pending_data(timeout_seconds=10):
    """清理超过 timeout_seconds 秒未凑齐的帧"""
    global pending_data
    now = time.time()
    expired_seqs = [seq for seq, data in pending_data.items()
                    if now - data.get('_time', 0) > timeout_seconds]
    for seq in expired_seqs:
        logger.warning(f"帧 {seq} 超时未凑齐，丢弃")
        del pending_data[seq]
    return len(expired_seqs)


#====================缓存，发给电脑，标记为已发====================
def  save_and_sent(temp, humid, lux):
    global remote_client
    # -------- 缓存数据 --------
    record_id = cache_data(temp, humid, lux)
    if  record_id is not None:

        # -------- 网络与发送 --------
        if is_network_available():
            # 确保 MQTT 连接
            if not remote_client.is_connected():
                connect_mqtt(remote_client, REMOTE_BROKER, REMOTE_PORT, KEEPALIVE)

            if remote_client.is_connected():
                # 发送 MQTT
                temp_ok = remote_client.publish("sensor/temperature",
                                              payload=str(temp)).rc == mqtt.MQTT_ERR_SUCCESS
                humid_ok = remote_client.publish("sensor/humidity",
                                               payload=str(humid)).rc == mqtt.MQTT_ERR_SUCCESS
                lux_ok = remote_client.publish("sensor/lux", payload=str(lux)).rc == mqtt.MQTT_ERR_SUCCESS

                if temp_ok and humid_ok and lux_ok:

                    # 写入 InfluxDB（使用当前时间）
                    if write_to_influx(temp, humid, lux, datetime.now(timezone.utc)):
                        # 标记发送成功
                        mark_as_sent([record_id])
                        logger.info(f"已发送 -> 温度: {temp}℃, 湿度: {humid}%, 亮度: {lux}")
                    else:
                        logger.warning("InfluxDB 写入失败，，数据保留缓存等待补发")
                        return
                else:
                    logger.warning("当前数据 MQTT 发送失败，保留在缓存中等待补发")
                    return
            else:
                logger.warning("MQTT 未连接，数据已缓存")
                return
        else:
            logger.warning("网络不可用，数据已缓存至本地")
            return
    else:
        logger.error("缓存失败")
        return



# ==================== 主程序 ====================
def main():

    global local_client, remote_client

    # 初始化数据库
    init_db()
    logger.info("数据库初始化完成")

    # 初始化 InfluxDB
    if not init_influx():
        logger.critical("无法连接 InfluxDB，程序退出")
        sys.exit(1)

    # 初始化 remote_MQTT 客户端
    remote_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if not connect_mqtt(remote_client,REMOTE_BROKER, REMOTE_PORT, KEEPALIVE):
        logger.warning("remote_MQTT 初始连接失败，将在主循环中重试")

    # 初始化 local_MQTT 客户端
    local_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    local_client.on_message = on_message_local
    if  connect_mqtt(local_client, LOCAL_BROKER, LOCAL_PORT, KEEPALIVE):
        local_client.subscribe("sensor/#")
    else:
        logger.warning("local_MQTT 初始连接失败，将在主循环中重试")

    # 定时器变量
    last_resend_time = time.time()
    last_clean_time = time.time()
    last_pending_clean_time = time.time()
    loop_count = 0

    logger.info("remote_MQTT Publisher 启动，开始发送数据...")

    try:
        while True:
            loop_count += 1
            # 每 60 个循环（约 5 分钟）报告一次状态
            if loop_count % 60 == 0:
                unsent = get_unsent_count()
                logger.info(f"状态报告: 运行约 {loop_count * SLEEP_INTERVAL} 秒, 未发送缓存 {unsent} 条")

            '''
            # -------- 读取传感器 --------
            
            temp, humid = read_dth22()
            lux = read_lux()
           
            

            if temp is None or humid is None:
                logger.warning("温湿度读取失败，跳过本帧")
                time.sleep(SLEEP_INTERVAL)
                continue
            if lux is None:
                lux = -1.0
           '''
            
            # 每隔 30 秒清理一次超时帧（使用时间间隔控制）
            if time.time() - last_pending_clean_time >= 30:
                cleaned = clean_pending_data(timeout_seconds=10)
                if cleaned > 0:
                    logger.info(f"清理了 {cleaned} 个超时帧")
                last_pending_clean_time = time.time()
                
            # -------- 补发检查 --------
            if remote_client.is_connected() and time.time() - last_resend_time >= RESEND_INTERVAL:
                if is_network_available():
                    resend_cached_data(remote_client, limit=200)
                last_resend_time = time.time()

            # -------- 清理旧数据 --------
            if time.time() - last_clean_time >= CLEAN_INTERVAL:
                clean_old_sent_data(days_to_keep=1)
                last_clean_time = time.time()

            # -------- 休眠 --------
            time.sleep(SLEEP_INTERVAL)


    except KeyboardInterrupt:
        logger.info("用户停止程序")
    except Exception as e:
        logger.critical(f"主循环发生未捕获异常: {e}", exc_info=True)
    finally:
        # 清理资源
        global db_conn
        if db_conn:
            db_conn.close()
        if remote_client:
            remote_client.loop_stop()
            remote_client.disconnect()
        if local_client:
            local_client.loop_stop()
            local_client.disconnect()
        if influx_client:
            influx_client.close()
        logger.info("MQTT Publisher 已退出")


if __name__ == "__main__":
    main()
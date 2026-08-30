===================================================================
  端边云物联网环境监测系统 - 项目说明
===================================================================

【项目简介】
基于 ESP32 + 树莓派 + MQTT + InfluxDB + Grafana 的端边云物联网环境监测系统。
实现温度、湿度、光照的实时采集、边缘缓存、云端存储与可视化。

【系统架构】
ESP32 (传感器节点) 
    │
    │  MQTT over WiFi
    ▼
树莓派 4B (边缘网关)
    │
    │  断网本地缓存 (SQLite) + 联网自动补发
    ▼
电脑 / 云服务器 (InfluxDB + Grafana)


【硬件清单】
设备       - -      型号         --   数量 

 边缘网关    -- 树莓派 4B (4GB) --1    
传感器节点   --    ESP32-S3 N16R8    --1    
温湿度传感器   --DHT22     --  1    
光照传感器   -- BH1750 (GY-302)  -- 1    


【软件技术栈】
设备端:   C++ (PlatformIO/Arduino)
边缘端:   Python 3.14 + Paho-MQTT
消息中间件: Mosquitto
时序数据库: InfluxDB 2.7
可视化:   Grafana
容器化:   Docker Compose

【核心功能】
1. ESP32 每 5 秒采集温湿度 + 光照数据
2. 树莓派作为 MQTT 网关，接收 ESP32 数据
3. 断网本地缓存（SQLite）：网络中断时自动存储
4. 联网自动补发：网络恢复后补发历史数据
5. 数据持久化存储到 InfluxDB
6. Grafana 实时可视化大屏
7. Systemd 服务实现开机自启
8. Docker Compose 一键部署云端环境

【项目结构】
iot_project/
├── README.txt                     # 项目说明
├── docker-compose.yml             # 电脑端容器编排
├── esp32/
│   └── esp32-sensor-node/         # ESP32 完整项目
│       ├── platformio.ini
│       └── src/
│           └── main.cpp
├── src/
│   ├── mqtt_publisher.py          # 树莓派网关主程序
│   ├── read_dht22_final.py        # DHT22 测试脚本
│   └── test_light.py              # BH1750 测试脚本
└── docs/
    └── images/
        └── grafana-dashboard.png  # Grafana 截图

【快速开始 - 树莓派端】
# 克隆项目
git clone https://github.com/54055136711/esp32-raspberrypi-mqtt-grafana.git
cd esp32-raspberrypi-mqtt-grafana

# 安装 Python 依赖
python3 -m venv iot_env
source iot_env/bin/activate
pip install -r requirements.txt

# 配置环境变量
export INFLUXDB_TOKEN="your-influxdb-token"

# 启动服务
sudo systemctl start mqtt_publisher.service
sudo systemctl enable mqtt_publisher.service

【快速开始 - 电脑端 (Docker Compose)】
docker compose up -d
docker ps

【环境变量说明】
INFLUXDB_TOKEN   InfluxDB 访问 Token
INFLUXDB_URL     InfluxDB 服务地址 (默认: http://localhost:8086)
INFLUXDB_ORG     InfluxDB 组织 (默认: my_org)
INFLUXDB_BUCKET  InfluxDB 存储桶 (默认: iot_data)

【遇到的问题与解决方案】                                                
一  DHT22 读取超时  ：
     在 DATA 和 VCC 之间添加 4.7kΩ 上拉电阻       

二  MQTT 端口被保留  ：
 Windows 系统保留端口 (1848-1947)，改用 18883 

三 ESP32 无法解析  .local 域名 ：   
  | 使用 IP 地址替代 .local 域名                 


【已知限制】
1. 目前仅支持单 ESP32 节点，多节点需修改主题格式
2. 数据保留策略默认永久，需手动配置过期时间

【未来计划】
- 增加多个 ESP32 节点，实现多房间监控
- 接入告警系统
- 使用 Pandas 做历史数据分析

===================================================================
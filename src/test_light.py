import smbus2
import time

bus = smbus2.SMBus(1)
addr = 0x23

def read_lux():
    """读取光照强度 (单位: lux)"""
    data=bus.read_i2c_block_data(addr,0x10,2)
    lux_raw = (data[0]<<8) | data[1]
    lux =lux_raw/1.2
    return round(lux,2)

if __name__=="__main__":
    print("开始读取 BH1750 光照数据，按 Ctrl+C 停止...")
    while True:
        try:
            lux = read_lux()
            print(f"☀️ 光照强度: {lux} lx")
            time.sleep(1)
        except    KeyboardInterrupt:
            print("程序已停止")
            break
        except Exception as e:
            print(f"读取出错: {e}")
            time.sleep(1)
import time
import board
import adafruit_dht

# 初始化传感器（GPIO4）
dht = adafruit_dht.DHT22(board.D4, use_pulseio=False)

def read_dht22(max_retries=10):
    """尝试最多 max_retries 次，直到成功读到有效数据"""
    for attempt in range(max_retries):
        try:
            temp = dht.temperature
            humid = dht.humidity
            if humid is not None and temp is not None:
                return temp, humid
        except RuntimeError:
            # 校验错误或超时，继续重试
            pass
        time.sleep(0.2)  # 短暂等待后重试
    return None, None  # 所有尝试都失败

print("开始读取 DHT22（带重试机制）...")
while True:
    temp, humid = read_dht22()
    if temp is not None:
        print(f"温度: {temp:.1f}°C, 湿度: {humid:.1f}%")
    else:
        print("连续续读取失败，等待后重试...")
    time.sleep(2)

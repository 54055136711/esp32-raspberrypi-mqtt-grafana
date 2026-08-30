#include <Arduino.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <BH1750.h>
#include <Wire.h>
#include <WiFi.h>

//WIFI 配置
const char* WIFI_SSID = "wifi-name";
const char* WIFI_PASSWORD= "wifi-password";

//MQTT 配置（指向树莓派）
const char* MQTT_BROKER="rpi4b.local";
const int MQTT_PORT= 1883 ;
const char* MQTT_TOPIC_TEMP = "sensor/temperature";
const char* MQTT_TOPIC_HUMID = "sensor/humidity";
const char* MQTT_TOPIC_LUX =  "sensor/lux";

//传感器引脚配置
#define DHTPIN 5
#define DHTTYPE DHT22
#define I2C_SDA 8
#define I2C_SCL 9

//全局对象
WiFiClient espClient;
BH1750 lightMeter;
DHT dht(DHTPIN, DHTTYPE);
PubSubClient mqttClient(espClient);

unsigned long lastPublishTime = 0;
const unsigned long  PUBLISH_INTERVAL = 5000;
unsigned long lastMQTTAttempt = 0;
const unsigned long MQTT_RETRY_INTERVAL = 5000;



void connect_wifi() //WiFi 连接
{
    Serial.print("正在连接 WiFi:");
    Serial.println(WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    int attempts =0;
    while(WiFi.status() != WL_CONNECTED )
    {
        delay(500);
        Serial.print(".");
        attempts++;

        if(attempts >=40 )
        {
            Serial.println("\n超时,重新发起连接...");
            ESP.restart();
        }
    }

    Serial.println("\n WiFi 已连接！");
    Serial.print("IP: ");
    Serial.print(WiFi.localIP());
}

void connect_MQTT() //MQTT 连接（连接到树莓派）
{
    if (mqttClient.connected()) return; 
    //每 5 秒尝试一次
    if (millis() - lastMQTTAttempt < MQTT_RETRY_INTERVAL) return; 
    lastMQTTAttempt = millis();
    
    Serial.print("正在连接树莓派 MQTT Broker: ");
    Serial.println(MQTT_BROKER);
    String clientId = "ESP32S3-" + String(esp_random(), HEX);

    if (mqttClient.connect(clientId.c_str())) {
        Serial.println("已连接到树莓派 MQTT");
        
    } else {
        Serial.print(" 连接失败，状态码: ");
        Serial.println(mqttClient.state());
    }

}

//发布数据 
void publish_SensorData()
{
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature();
    if( isnan(humidity) || isnan(temperature))
    {
        Serial.println("DHT22 读取失败");
        return;
    }

    float lux = lightMeter.readLightLevel();
    if(lux < 0) lux =-1.0;
     
    if(mqttClient.connected())
    {
        int seq = esp_random() % 1000;  // 随机帧号
        mqttClient.publish(MQTT_TOPIC_TEMP, (String(temperature) + "," + String(seq)).c_str());
        mqttClient.publish(MQTT_TOPIC_HUMID, (String(humidity) + "," + String(seq)).c_str());
        mqttClient.publish(MQTT_TOPIC_LUX, (String(lux) + "," + String(seq)).c_str());

        Serial.print("已发送 -> 温度: ");
        Serial.print(temperature);
        Serial.print("℃, 湿度: ");
        Serial.print(humidity);
        Serial.print("%, 光照: ");
        Serial.print(lux);
        Serial.println(" lx");
    }
         
}


void setup() {
   Serial.begin(115200);
   delay(1000);
   Serial.println("\n=== ESP32-S3 传感器节点启动 ===");

   Wire.begin(I2C_SDA,I2C_SCL);
   if(lightMeter.begin())  
        Serial.println("BH1750 初始化成功");
   else
        Serial.println("BH1750 初始化失败");    

   dht.begin();   
   Serial.println("DHT22 传感器初始化完成");  

   connect_wifi();

   IPAddress brokerIP;
   Serial.print("正在解析 MQTT Broker 域名: ");
   Serial.println(MQTT_BROKER);
   if(WiFi.hostByName(MQTT_BROKER,brokerIP))
   {
       Serial.print("解析成功,IP: ");
       Serial.println(brokerIP);
       
   }else{
       Serial.println("DNS 解析失败!启用默认IP: 树莓派-ip");
       brokerIP.fromString("树莓派-ip");
   }
 
   mqttClient.setServer(brokerIP, MQTT_PORT);
   connect_MQTT();

   Serial.println("系统启动完成");
}

void loop() {
    if(! mqttClient.connected())  
        connect_MQTT();
    mqttClient.loop();
    
    if(millis()-lastPublishTime >= PUBLISH_INTERVAL)
    {
        publish_SensorData();
        lastPublishTime=millis();
    }
    delay(10);
    
}
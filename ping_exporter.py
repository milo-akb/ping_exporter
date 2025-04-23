from prometheus_client import start_http_server, Gauge
import subprocess
import time
import threading
import statistics
import os

#-----------------------------------------------Targets-------------------------------------------------------#
PING_TARGETS = os.getenv("PING_TARGETS", "8.8.8.8,1.1.1.1").split(",")
PING_INTERVAL = int(os.getenv("PING_INTERVAL", 10))

#-----------------------------------------------Metrics-------------------------------------------------------#
ping_latency_avg = Gauge('ping_latency_avg_ms', 'Average ping latency in ms', ['target'])
ping_latency_min = Gauge('ping_latency_min_ms', 'Minimum ping latency in ms', ['target'])
ping_latency_max = Gauge('ping_latency_max_ms', 'Maximum ping latency in ms', ['target'])
ping_latency_jitter = Gauge('ping_latency_jitter_ms', 'Ping jitter (std dev) in ms', ['target'])
ping_packet_loss = Gauge('ping_packet_loss_percent', 'Packet loss percentage', ['target'])
ping_success = Gauge('ping_success_rate', 'Ping success rate (0-1)', ['target'])

def ping_target(target):
    while True:
        try:
            result = subprocess.run(
                ['ping', '-c', '5', '-W', '1', target],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode == 0:
                latencies = [
                    float(line.split('time=')[1].split(' ')[0])
                    for line in result.stdout.splitlines()
                    if "time=" in line
                ]

                loss_line = next((line for line in result.stdout.splitlines() if "packet loss" in line), None)
                loss_percent = float(loss_line.split('%')[0].split(' ')[-1]) if loss_line else 100.0

                if latencies:
                    ping_latency_avg.labels(target=target).set(sum(latencies) / len(latencies))
                    ping_latency_min.labels(target=target).set(min(latencies))
                    ping_latency_max.labels(target=target).set(max(latencies))
                    ping_latency_jitter.labels(target=target).set(statistics.stdev(latencies) if len(latencies) > 1 else 0)
                else:
                    for metric in [ping_latency_avg, ping_latency_min, ping_latency_max, ping_latency_jitter]:
                        metric.labels(target=target).set(-1)

                ping_packet_loss.labels(target=target).set(loss_percent)
                ping_success.labels(target=target).set(1 - loss_percent / 100)
            else:
                for metric in [ping_latency_avg, ping_latency_min, ping_latency_max, ping_latency_jitter]:
                    metric.labels(target=target).set(-1)
                ping_packet_loss.labels(target=target).set(100.0)
                ping_success.labels(target=target).set(0)

        except Exception as e:
            for metric in [ping_latency_avg, ping_latency_min, ping_latency_max, ping_latency_jitter, ping_packet_loss, ping_success]:
                metric.labels(target=target).set(-1)

        time.sleep(PING_INTERVAL)

if __name__ == '__main__':
    start_http_server(8000)
    print("ping exporter running at http://localhost:8000/metrics")

    for tgt in PING_TARGETS:
        threading.Thread(target=ping_target, args=(tgt.strip(),), daemon=True).start()

    while True:
        time.sleep(60)

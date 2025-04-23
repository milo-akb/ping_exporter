import os
import time
import threading
import subprocess
import statistics
import yaml
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from prometheus_client import start_http_server, Gauge

#-----------------------------------------------Targets-------------------------------------------------------#
CONFIG_PATH = 'config.yaml'
PING_INTERVAL = int(os.getenv("PING_INTERVAL", 10))

#-----------------------------------------------Logging-------------------------------------------------------#
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

#-----------------------------------------------Metrics-------------------------------------------------------#
lat_avg = Gauge('ping_latency_avg_ms', 'Average latency', ['address', 'name', 'location'])
lat_min = Gauge('ping_latency_min_ms', 'Min latency', ['address', 'name', 'location'])
lat_max = Gauge('ping_latency_max_ms', 'Max latency', ['address', 'name', 'location'])
lat_jitter = Gauge('ping_latency_jitter_ms', 'Jitter', ['address', 'name', 'location'])
pkt_loss = Gauge('ping_packet_loss_percent', 'Packet loss %', ['address', 'name', 'location'])
ping_success = Gauge('ping_success_rate', 'Ping success rate (0-1)', ['address', 'name', 'location'])


active_threads = {}
thread_stoppers = {}
targets_lock = threading.Lock()

def load_config():
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f)
        return data.get('targets', [])

def update_targets():
    new_targets = load_config()
    new_keys = set(t['address'] for t in new_targets)

    with targets_lock:
        # Start new threads
        for t in new_targets:
            key = t['address']
            if key not in active_threads:
                logging.info(f"Starting ping thread for {key}")
                stop_event = threading.Event()
                thread = threading.Thread(target=ping_loop, args=(t, stop_event), daemon=True)
                active_threads[key] = thread
                thread_stoppers[key] = stop_event
                thread.start()

        # Stop and remove threads for removed targets
        removed_keys = set(active_threads.keys()) - new_keys
        for key in removed_keys:
            logging.info(f"Stopping ping thread for removed target {key}")
            thread_stoppers[key].set()  # Signal thread to stop
            active_threads[key].join()  # Wait for it to finish
            del active_threads[key]
            del thread_stoppers[key]
            # Optionally clear metrics
            for metric in [lat_avg, lat_min, lat_max, lat_jitter, pkt_loss, ping_success]:
                metric.remove(address=key, name='', location='')  # Assumes labels were empty

class ConfigWatcher(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(CONFIG_PATH):
            logging.info("Config changed. Reloading...")
            update_targets()

def ping_loop(target, stop_event):
    address = target['address']
    labels = target.get('labels', {})
    label_values = {
        'address': address,
        'name': labels.get('name', ''),
        'location': labels.get('location', '')
    }

    while not stop_event.is_set():
        try:
            result = subprocess.run(['ping', '-c', '5', '-W', '1', address],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                latencies = [float(line.split('time=')[1].split(' ')[0])
                             for line in result.stdout.splitlines() if "time=" in line]
                loss_line = next((line for line in result.stdout.splitlines() if "packet loss" in line), "")
                loss = float(loss_line.split('%')[0].split()[-1]) if loss_line else 100.0

                if latencies:
                    lat_avg.labels(**label_values).set(sum(latencies) / len(latencies))
                    lat_min.labels(**label_values).set(min(latencies))
                    lat_max.labels(**label_values).set(max(latencies))
                    lat_jitter.labels(**label_values).set(statistics.stdev(latencies) if len(latencies) > 1 else 0)
                else:
                    for m in [lat_avg, lat_min, lat_max, lat_jitter]:
                        m.labels(**label_values).set(-1)
                pkt_loss.labels(**label_values).set(loss)
                ping_success.labels(**label_values).set(1 - loss / 100)
            else:
                for m in [lat_avg, lat_min, lat_max, lat_jitter]:
                    m.labels(**label_values).set(-1)
                pkt_loss.labels(**label_values).set(100.0)
                ping_success.labels(**label_values).set(0)
        except Exception as e:
            logging.error(f"Error pinging {address}: {e}")
            for m in [lat_avg, lat_min, lat_max, lat_jitter, pkt_loss, ping_success]:
                m.labels(**label_values).set(-1)

        stop_event.wait(PING_INTERVAL)

if __name__ == "__main__":
    start_http_server(8000)
    logging.info("Exporter running at http://localhost:8000/metrics")
    update_targets()

    observer = Observer()
    observer.schedule(ConfigWatcher(), '.', recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        observer.stop()
        observer.join()
        with targets_lock:
            for key, stop_event in thread_stoppers.items():
                stop_event.set()
            for key, thread in active_threads.items():
                thread.join()
        logging.info("Shutdown complete.")

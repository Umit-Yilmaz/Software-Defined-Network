# /home/umit/SDN_Project/dashboard_app.py

from flask import Flask, render_template, jsonify, request
import psutil
import requests
import json
import time
import subprocess
import re
import threading
from datetime import datetime, timedelta
from collections import defaultdict, deque
import sqlite3
import os
from flask_socketio import SocketIO, emit
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sdn_dashboard_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ryu kontrolcüsünün REST API adresi
# Eğer Ryu Docker içinde farklı bir IP'de veya farklı bir ağdaysa, bu IP adresini güncellemeniz gerekebilir.
# Genellikle host'tan erişimde 127.0.0.1 veya Docker iç ağındaki servis adı kullanılır.
RYU_API_URL = "http://localhost:8080" 

# Database initialization
DB_PATH = "sdn_metrics.db"

def init_database():
    """Initialize SQLite database for storing metrics history"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # System metrics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                cpu_usage REAL,
                memory_percent REAL,
                network_bytes_sent INTEGER,
                network_bytes_recv INTEGER,
                disk_usage REAL,
                network_packets_sent INTEGER,
                network_packets_recv INTEGER
            )
        ''')
        
        # Flow statistics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flow_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                dpid TEXT,
                table_id INTEGER,
                packet_count INTEGER,
                byte_count INTEGER,
                flow_count INTEGER,
                match_fields TEXT,
                actions TEXT
            )
        ''')
        
        # Port statistics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS port_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                dpid TEXT,
                port_no INTEGER,
                rx_packets INTEGER,
                tx_packets INTEGER,
                rx_bytes INTEGER,
                tx_bytes INTEGER,
                rx_dropped INTEGER,
                tx_dropped INTEGER,
                rx_errors INTEGER,
                tx_errors INTEGER
            )
        ''')
        
        # Network events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS network_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                event_type TEXT,
                dpid TEXT,
                description TEXT,
                severity TEXT
            )
        ''')
        
        # Topology changes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS topology_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                change_type TEXT,
                node_id TEXT,
                details TEXT
            )
        ''')
        
        conn.commit()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Error initializing database: {e}")
    finally:
        if conn:
            conn.close()

# In-memory data structures for real-time monitoring
system_metrics_history = deque(maxlen=100)
flow_stats_history = defaultdict(lambda: deque(maxlen=50))
port_stats_history = defaultdict(lambda: deque(maxlen=50))
network_events = deque(maxlen=200)
topology_state = {}

# Enhanced topology with more detailed information
MININET_TOPOLOGY = {
    "hosts": [
        {'name': 'c1', 'ip': '10.0.0.1/24', 'mac': '00:00:00:00:00:01', 'type': 'client'},
        {'name': 'c2', 'ip': '10.0.0.2/24', 'mac': '00:00:00:00:00:02', 'type': 'client'},
        {'name': 'sva', 'ip': '10.0.0.10/24', 'mac': '00:00:00:00:00:10', 'type': 'server'},
        {'name': 'svb', 'ip': '10.0.0.11/24', 'mac': '00:00:00:00:00:11', 'type': 'server'}
    ],
    "switches": [
        {'name': 's1', 'dpid': '0000000000000001', 'type': 'edge'},
        {'name': 's2', 'dpid': '0000000000000002', 'type': 'core'},
        {'name': 's3', 'dpid': '0000000000000003', 'type': 'edge'}
    ],
    "links": [
        {'from': 'c1', 'to': 's1', 'bandwidth': '100Mbps', 'latency': '1ms'},
        {'from': 'c2', 'to': 's1', 'bandwidth': '100Mbps', 'latency': '1ms'},
        {'from': 'sva', 'to': 's3', 'bandwidth': '1Gbps', 'latency': '0.5ms'},
        {'from': 'svb', 'to': 's3', 'bandwidth': '1Gbps', 'latency': '0.5ms'},
        {'from': 's1', 'to': 's2', 'bandwidth': '10Gbps', 'latency': '2ms'},
        {'from': 's2', 'to': 's3', 'bandwidth': '10Gbps', 'latency': '2ms'}
    ]
}

def store_system_metrics(metrics):
    """Store system metrics in database"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO system_metrics 
            (cpu_usage, memory_percent, network_bytes_sent, network_bytes_recv, 
             disk_usage, network_packets_sent, network_packets_recv)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            metrics['cpu_usage'], metrics['memory_percent'],
            metrics['network_bytes_sent'], metrics['network_bytes_recv'],
            metrics['disk_usage'], metrics['network_packets_sent'],
            metrics['network_packets_recv']
        ))
        conn.commit()
        logger.info("System metrics stored.")
    except Exception as e:
        logger.error(f"Error storing system metrics: {e}")
    finally:
        if conn:
            conn.close()

def store_flow_stats(dpid, flow_data, flow_count):
    """Store flow statistics for a given DPID in database"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # For simplicity, we store aggregate counts and one example flow entry if available
        # You might want a more sophisticated way to store all flow entries if needed for historical detail
        example_match_fields = ""
        example_actions = ""
        total_packet_count = sum(flow.get('packet_count', 0) for flow in flow_data)
        total_byte_count = sum(flow.get('byte_count', 0) for flow in flow_data)

        if flow_data:
            first_flow = flow_data[0]
            example_match_fields = json.dumps(first_flow.get('match', {}))
            example_actions = json.dumps(first_flow.get('instructions', []))

        cursor.execute('''
            INSERT INTO flow_stats 
            (dpid, table_id, packet_count, byte_count, flow_count, match_fields, actions)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            dpid, 0, total_packet_count, total_byte_count, flow_count, 
            example_match_fields, example_actions
        ))
        conn.commit()
        logger.info(f"Flow stats stored for DPID {dpid}: {flow_count} flows.")
    except Exception as e:
        logger.error(f"Error storing flow stats for DPID {dpid}: {e}")
    finally:
        if conn:
            conn.close()

def store_port_stats(dpid, port_data):
    """Store port statistics for a given DPID in database"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for port in port_data:
            cursor.execute('''
                INSERT INTO port_stats 
                (dpid, port_no, rx_packets, tx_packets, rx_bytes, tx_bytes, 
                 rx_dropped, tx_dropped, rx_errors, tx_errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                dpid, port.get('port_no', -1), port.get('rx_packets', 0), port.get('tx_packets', 0),
                port.get('rx_bytes', 0), port.get('tx_bytes', 0), port.get('rx_dropped', 0),
                port.get('tx_dropped', 0), port.get('rx_errors', 0), port.get('tx_errors', 0)
            ))
        conn.commit()
        logger.info(f"Port stats stored for DPID {dpid}.")
    except Exception as e:
        logger.error(f"Error storing port stats for DPID {dpid}: {e}")
    finally:
        if conn:
            conn.close()

def store_network_event(event_type, dpid, description, severity='info'):
    """Store network events in database"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO network_events (event_type, dpid, description, severity)
            VALUES (?, ?, ?, ?)
        ''', (event_type, dpid, description, severity))
        conn.commit()
        logger.info(f"Network event stored: {event_type} - {description} (Severity: {severity})")
        
        # Add to in-memory queue for real-time updates
        network_events.append({
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'dpid': dpid,
            'description': description,
            'severity': severity
        })
        
        # Emit to connected clients
        socketio.emit('network_event', {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'dpid': dpid,
            'description': description,
            'severity': severity
        })
    except Exception as e:
        logger.error(f"Error storing network event: {e}")
    finally:
        if conn:
            conn.close()

def detect_anomalies(current_metrics, history):
    """Simple anomaly detection based on statistical thresholds"""
    anomalies = []
    
    if len(history) < 10:
        return anomalies
    
    # CPU usage anomaly
    cpu_values = [m['cpu_usage'] for m in history if 'cpu_usage' in m]
    if cpu_values:
        cpu_avg = sum(cpu_values) / len(cpu_values)
        if current_metrics['cpu_usage'] > cpu_avg * 1.5 and current_metrics['cpu_usage'] > 80:
            anomalies.append({
                'type': 'high_cpu',
                'value': current_metrics['cpu_usage'],
                'threshold': 80,
                'description': f"High CPU usage detected: {current_metrics['cpu_usage']:.1f}%"
            })
    
    # Memory usage anomaly
    if current_metrics['memory_percent'] > 90:
        anomalies.append({
            'type': 'high_memory',
            'value': current_metrics['memory_percent'],
            'threshold': 90,
            'description': f"High memory usage detected: {current_metrics['memory_percent']:.1f}%"
        })
    
    return anomalies

@app.route('/')
def index():
    return render_template('index_dashboard.html')

@app.route('/api/system_metrics')
def get_system_metrics():
    """Enhanced system metrics with additional information"""
    try:
        # Basic metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory_info = psutil.virtual_memory()
        network_io = psutil.net_io_counters()
        disk_info = psutil.disk_usage('/')
        
        # Additional metrics
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        boot_time = psutil.boot_time()
        
        metrics = {
            'cpu_usage': cpu_percent,
            'cpu_count': cpu_count,
            'cpu_freq': cpu_freq.current if cpu_freq else 0,
            'memory_total': memory_info.total,
            'memory_available': memory_info.available,
            'memory_percent': memory_info.percent,
            'memory_used': memory_info.used,
            'network_bytes_sent': network_io.bytes_sent,
            'network_bytes_recv': network_io.bytes_recv,
            'network_packets_sent': network_io.packets_sent,
            'network_packets_recv': network_io.packets_recv,
            'disk_total': disk_info.total,
            'disk_used': disk_info.used,
            'disk_usage': (disk_info.used / disk_info.total) * 100,
            'uptime': time.time() - boot_time,
            'timestamp': int(time.time() * 1000)
        }
        
        # Store metrics
        system_metrics_history.append(metrics)
        store_system_metrics(metrics)
        
        # Detect anomalies
        anomalies = detect_anomalies(metrics, list(system_metrics_history))
        metrics['anomalies'] = anomalies
        
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Error getting system metrics: {e}")
        return jsonify({'error': str(e)}), 500


# Enhanced debugging and fixes for flow statistics in dashboard_app.py

# Replace the get_ryu_metrics function with this enhanced version:

@app.route('/api/ryu_metrics')
def get_ryu_metrics():
    """Enhanced Ryu metrics with detailed analysis and better flow handling"""
    metrics = {
        'switches': [],
        'flow_stats': {},
        'port_stats': {},
        'topology_summary': {},
        'performance_metrics': {},
        'alerts': []
    }
    
    try:
        # Get switches information with enhanced details
        switches_api_url = f"{RYU_API_URL}/v1.0/topology/switches"
        logger.info(f"Attempting to fetch switches from Ryu API: {switches_api_url}")
        switches_response = requests.get(switches_api_url, timeout=5)
        switches_response.raise_for_status()
        ryu_switches = switches_response.json()
        logger.info(f"Successfully fetched {len(ryu_switches)} switches.")

        total_flows = 0
        total_packets = 0
        total_bytes = 0

        for s in ryu_switches:
            dpid = s['dpid']
            switch_info = {
                'dpid': dpid,
                'ports': s.get('ports', []),
                'status': 'connected',
                'last_seen': datetime.now().isoformat(),
                'flow_count': 0,
                'packet_rate': 0,
                'byte_rate': 0
            }
            
            # --- Enhanced Flow Statistics ---
            try:
                flow_stats_api_url = f"{RYU_API_URL}/stats/flow/{dpid}"
                logger.info(f"Fetching flow stats for DPID {dpid} from {flow_stats_api_url}")
                flow_stats_response = requests.get(flow_stats_api_url, timeout=5)
                flow_stats_response.raise_for_status()
                
                # Log the raw response for debugging
                raw_response = flow_stats_response.text
                logger.info(f"Raw flow stats response for DPID {dpid}: {raw_response[:500]}...")
                
                flow_stats = flow_stats_response.json()
                logger.info(f"Parsed flow stats keys: {list(flow_stats.keys())}")
                
                # Try different ways to access flow data based on Ryu API response format
                flow_data = []
                if isinstance(flow_stats, dict):
                    # Method 1: Direct DPID key access
                    if dpid in flow_stats:
                        flow_data = flow_stats[dpid]
                        logger.info(f"Found flows using DPID key: {len(flow_data)} flows")
                    # Method 2: String DPID key access  
                    elif str(int(dpid, 16)) in flow_stats:
                        flow_data = flow_stats[str(int(dpid, 16))]
                        logger.info(f"Found flows using decimal DPID key: {len(flow_data)} flows")
                    # Method 3: Direct list access if response is wrapped
                    elif len(flow_stats) == 1 and isinstance(list(flow_stats.values())[0], list):
                        flow_data = list(flow_stats.values())[0]
                        logger.info(f"Found flows using first value: {len(flow_data)} flows")
                    else:
                        logger.warning(f"Could not find flow data in response keys: {list(flow_stats.keys())}")
                elif isinstance(flow_stats, list):
                    # Sometimes Ryu returns a direct list
                    flow_data = flow_stats
                    logger.info(f"Flow stats is direct list: {len(flow_data)} flows")
                
                # Process flow data
                if flow_data:
                    switch_flow_count = len(flow_data)
                    switch_packets = 0
                    switch_bytes = 0
                    
                    # Enhanced flow processing with better error handling
                    processed_flows = []
                    for i, flow in enumerate(flow_data):
                        try:
                            processed_flow = {
                                'table_id': flow.get('table_id', 0),
                                'priority': flow.get('priority', 0),
                                'match': flow.get('match', {}),
                                'instructions': flow.get('instructions', []),
                                'packet_count': flow.get('packet_count', 0),
                                'byte_count': flow.get('byte_count', 0),
                                'duration_sec': flow.get('duration_sec', 0),
                                'duration_nsec': flow.get('duration_nsec', 0),
                                'idle_timeout': flow.get('idle_timeout', 0),
                                'hard_timeout': flow.get('hard_timeout', 0),
                                'cookie': flow.get('cookie', 0)
                            }
                            
                            switch_packets += processed_flow['packet_count']
                            switch_bytes += processed_flow['byte_count']
                            processed_flows.append(processed_flow)
                            
                        except Exception as flow_error:
                            logger.error(f"Error processing flow {i} for DPID {dpid}: {flow_error}")
                            logger.error(f"Problematic flow data: {flow}")
                    
                    switch_info.update({
                        'flow_count': switch_flow_count,
                        'total_packets': switch_packets,
                        'total_bytes': switch_bytes
                    })
                    
                    metrics['flow_stats'][dpid] = processed_flows
                    total_flows += switch_flow_count
                    total_packets += switch_packets
                    total_bytes += switch_bytes
                    
                    logger.info(f"Successfully processed {len(processed_flows)} flows for DPID {dpid}")
                    logger.info(f"Flow stats summary - Packets: {switch_packets}, Bytes: {switch_bytes}")
                    
                else:
                    logger.warning(f"No flow data found for DPID {dpid}")
                    metrics['flow_stats'][dpid] = []
                
                # Store flow stats history
                flow_stats_history[dpid].append({
                    'timestamp': time.time(),
                    'flow_count': switch_info['flow_count'],
                    'packets': switch_info['total_packets'],
                    'bytes': switch_info['total_bytes']
                })
                
                # Store in database
                store_flow_stats(dpid, metrics['flow_stats'][dpid], switch_info['flow_count'])

            except requests.exceptions.RequestException as e:
                logger.error(f"Network error getting flow stats for switch {dpid}: {e}")
                metrics['flow_stats'][dpid] = []
                store_network_event('ryu_flow_error', dpid, f"Flow stats network error: {e}", 'warning')
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error for flow stats from DPID {dpid}: {e}")
                logger.error(f"Response content: {flow_stats_response.text[:1000]}")
                metrics['flow_stats'][dpid] = []
                store_network_event('ryu_json_error', dpid, f"Flow stats JSON error: {e}", 'warning')
            except Exception as e:
                logger.error(f"Unexpected error processing flow stats for DPID {dpid}: {e}")
                metrics['flow_stats'][dpid] = []
                store_network_event('ryu_flow_error', dpid, f"Flow stats processing error: {e}", 'error')

            # --- Port Statistics (keeping existing logic) ---
            try:
                port_stats_api_url = f"{RYU_API_URL}/stats/port/{dpid}"
                logger.info(f"Fetching port stats for DPID {dpid} from {port_stats_api_url}")
                port_stats_response = requests.get(port_stats_api_url, timeout=3)
                port_stats_response.raise_for_status()
                port_stats = port_stats_response.json()
                port_data = port_stats.get(str(dpid), [])
                
                metrics['port_stats'][dpid] = port_data
                
                # Calculate port utilization and detect issues
                for port in port_data:
                    if port.get('rx_errors', 0) > 0 or port.get('tx_errors', 0) > 0:
                        metrics['alerts'].append({
                            'type': 'port_errors',
                            'dpid': dpid,
                            'port': port.get('port_no'),
                            'rx_errors': port.get('rx_errors', 0),
                            'tx_errors': port.get('tx_errors', 0),
                            'description': f"Port {port.get('port_no')} on switch {dpid} has errors"
                        })
                
                # Store port stats history
                port_stats_history[dpid].append({
                    'timestamp': time.time(),
                    'ports': port_data
                })
                store_port_stats(dpid, port_data)

            except Exception as e:
                logger.error(f"Error getting port stats for switch {dpid}: {e}")
                metrics['port_stats'][dpid] = []

            metrics['switches'].append(switch_info)

        # Calculate network-wide performance metrics
        metrics['performance_metrics'] = {
            'total_switches': len(ryu_switches),
            'total_flows': total_flows,
            'total_packets': total_packets,
            'total_bytes': total_bytes,
            'avg_flows_per_switch': total_flows / len(ryu_switches) if ryu_switches else 0,
            'network_utilization': calculate_network_utilization(metrics['port_stats']),
            'packet_rate': calculate_packet_rate(),
            'throughput': calculate_throughput()
        }
        
        # Topology summary
        metrics['topology_summary'] = {
            'active_switches': len([s for s in metrics['switches'] if s['status'] == 'connected']),
            'total_ports': sum(len(s['ports']) for s in metrics['switches']),
            'total_links': len(MININET_TOPOLOGY['links']),
            'network_health': calculate_network_health(metrics)
        }

        logger.info(f"Final metrics summary - Total Flows: {total_flows}, Total Switches: {len(ryu_switches)}")

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Could not connect to Ryu API at {RYU_API_URL}: {e}")
        metrics['error'] = "Could not connect to Ryu API. Make sure Ryu is running with correct ports."
        store_network_event('ryu_connection_error', '', 'Ryu API connection error', 'error')
    except Exception as e:
        logger.error(f"Unexpected error in get_ryu_metrics: {e}")
        metrics['error'] = f"Unexpected error: {e}"
        store_network_event('ryu_general_error', '', f"General Ryu API error: {e}", 'error')
    
    return jsonify(metrics)


# Add this debugging endpoint to test flow stats directly
@app.route('/api/debug/flows/<dpid>')
def debug_flows(dpid):
    """Debug endpoint to check raw flow stats from Ryu"""
    try:
        flow_stats_api_url = f"{RYU_API_URL}/stats/flow/{dpid}"
        logger.info(f"Debug: Fetching flows from {flow_stats_api_url}")
        
        response = requests.get(flow_stats_api_url, timeout=5)
        
        debug_info = {
            'url': flow_stats_api_url,
            'status_code': response.status_code,
            'headers': dict(response.headers),
            'raw_content': response.text,
            'content_length': len(response.text)
        }
        
        if response.status_code == 200:
            try:
                parsed_json = response.json()
                debug_info['parsed_json'] = parsed_json
                debug_info['json_keys'] = list(parsed_json.keys()) if isinstance(parsed_json, dict) else 'not_dict'
                debug_info['json_type'] = str(type(parsed_json))
                
                # Try to extract flow data using different methods
                if isinstance(parsed_json, dict):
                    for key in [dpid, str(int(dpid, 16)), str(int(dpid, 16))]:
                        if key in parsed_json:
                            debug_info[f'flows_via_key_{key}'] = len(parsed_json[key]) if isinstance(parsed_json[key], list) else 'not_list'
                
            except json.JSONDecodeError as e:
                debug_info['json_error'] = str(e)
        
        return jsonify(debug_info)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Add this endpoint to test all switches at once
@app.route('/api/debug/all_flows')
def debug_all_flows():
    """Debug endpoint to check all switch flows"""
    debug_results = {}
    
    try:
        # First get all switches
        switches_response = requests.get(f"{RYU_API_URL}/v1.0/topology/switches", timeout=5)
        switches_response.raise_for_status()
        switches = switches_response.json()
        
        debug_results['switches_found'] = len(switches)
        debug_results['switch_dpids'] = [s['dpid'] for s in switches]
        
        # Then check flows for each switch
        for switch in switches:
            dpid = switch['dpid']
            try:
                flow_response = requests.get(f"{RYU_API_URL}/stats/flow/{dpid}", timeout=3)
                flow_data = flow_response.json()
                
                debug_results[f'switch_{dpid}'] = {
                    'status_code': flow_response.status_code,
                    'response_type': str(type(flow_data)),
                    'response_keys': list(flow_data.keys()) if isinstance(flow_data, dict) else 'not_dict',
                    'raw_response_sample': str(flow_data)[:200] + '...' if len(str(flow_data)) > 200 else str(flow_data)
                }
                
                # Try to count flows
                flow_count = 0
                if isinstance(flow_data, dict):
                    for key, value in flow_data.items():
                        if isinstance(value, list):
                            flow_count += len(value)
                elif isinstance(flow_data, list):
                    flow_count = len(flow_data)
                
                debug_results[f'switch_{dpid}']['estimated_flow_count'] = flow_count
                
            except Exception as e:
                debug_results[f'switch_{dpid}'] = {'error': str(e)}
        
        return jsonify(debug_results)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def calculate_network_utilization(port_stats):
    """Calculate overall network utilization"""
    total_capacity_bits_per_sec = 0
    total_used_bits_per_sec = 0
    
    # We need to calculate utilization based on rate, not just cumulative bytes.
    # This requires previous stats.
    # For a simple current utilization, we can assume instantaneous link capacity and consumed bytes.
    
    # Let's use the port_stats_history for a more accurate rate-based utilization
    # This is a simplified calculation and might need more robust logic for real-world scenarios.
    
    current_time = time.time()
    for dpid, history in port_stats_history.items():
        if len(history) >= 2:
            latest_stats = history[-1]
            previous_stats = history[-2]
            
            time_diff = latest_stats['timestamp'] - previous_stats['timestamp']
            
            if time_diff > 0:
                for current_port_stat in latest_stats['ports']:
                    port_no = current_port_stat.get('port_no')
                    previous_port_stat = next((p for p in previous_stats['ports'] if p.get('port_no') == port_no), None)
                    
                    if previous_port_stat:
                        # Convert bytes to bits for rate calculation
                        rx_bytes_diff = current_port_stat.get('rx_bytes', 0) - previous_port_stat.get('rx_bytes', 0)
                        tx_bytes_diff = current_port_stat.get('tx_bytes', 0) - previous_port_stat.get('tx_bytes', 0)
                        
                        port_bits_per_sec = (rx_bytes_diff + tx_bytes_diff) * 8 / time_diff
                        
                        # Assume a theoretical port capacity (e.g., 1 Gbps = 1,000,000,000 bits/sec)
                        # This should ideally come from switch port capabilities
                        assumed_port_capacity = 1_000_000_000 # 1 Gbps
                        
                        total_capacity_bits_per_sec += assumed_port_capacity
                        total_used_bits_per_sec += port_bits_per_sec
    
    return (total_used_bits_per_sec / total_capacity_bits_per_sec * 100) if total_capacity_bits_per_sec > 0 else 0

def calculate_packet_rate():
    """Calculate current packet rate across the network"""
    if len(system_metrics_history) < 2:
        return 0
    
    current = system_metrics_history[-1]
    previous = system_metrics_history[-2]
    time_diff = (current['timestamp'] - previous['timestamp']) / 1000  # Convert to seconds
    
    if time_diff > 0:
        packet_diff = (current.get('network_packets_sent', 0) + current.get('network_packets_recv', 0)) - \
                      (previous.get('network_packets_sent', 0) + previous.get('network_packets_recv', 0))
        return packet_diff / time_diff
    return 0

def calculate_throughput():
    """Calculate current network throughput (bits per second)"""
    if len(system_metrics_history) < 2:
        return 0
    
    current = system_metrics_history[-1]
    previous = system_metrics_history[-2]
    time_diff = (current['timestamp'] - previous['timestamp']) / 1000
    
    if time_diff > 0:
        byte_diff = (current.get('network_bytes_sent', 0) + current.get('network_bytes_recv', 0)) - \
                    (previous.get('network_bytes_sent', 0) + previous.get('network_bytes_recv', 0))
        return (byte_diff * 8) / time_diff  # Convert to bits per second
    return 0

def calculate_network_health(metrics):
    """Calculate overall network health score"""
    health_score = 100
    
    # Reduce score for each alert
    health_score -= len(metrics.get('alerts', [])) * 5
    
    # Reduce score for disconnected switches
    disconnected_switches = len([s for s in metrics['switches'] if s['status'] != 'connected'])
    health_score -= disconnected_switches * 20
    
    # Reduce score based on error rates (simplified)
    for dpid, ports in metrics.get('port_stats', {}).items():
        for port in ports:
            errors = port.get('rx_errors', 0) + port.get('tx_errors', 0)
            if errors > 0:
                health_score -= min(errors * 0.1, 10)  # Cap at 10 points per port
    
    return max(0, min(100, health_score))

@app.route('/api/topology')
def get_topology():
    """Enhanced topology with real-time status"""
    enhanced_topology = MININET_TOPOLOGY.copy()
    
    # Add real-time status information
    for switch in enhanced_topology['switches']:
        switch['status'] = 'unknown'
        switch['last_seen'] = None
        switch['flow_count'] = 0
    
    # Try to get real-time switch status from Ryu
    try:
        switches_response = requests.get(f"{RYU_API_URL}/v1.0/topology/switches", timeout=3)
        if switches_response.status_code == 200:
            ryu_switches = switches_response.json()
            ryu_dpids = {s['dpid'] for s in ryu_switches}
            
            for switch in enhanced_topology['switches']:
                if switch['dpid'] in ryu_dpids:
                    switch['status'] = 'active'
                    switch['last_seen'] = datetime.now().isoformat()
        else:
            logger.warning(f"Ryu topology API returned status {switches_response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting topology from Ryu API: {e}")
        # Topology status will remain 'unknown' or previous if error.
        # We don't return an error JSON here, just let the UI handle missing data.
    
    return jsonify(enhanced_topology)

@app.route('/api/historical_data')
def get_historical_data():
    """Get historical data for charts and analysis"""
    conn = None
    try:
        hours = request.args.get('hours', 24, type=int)
        metric_type = request.args.get('type', 'system')
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        since_time = datetime.now() - timedelta(hours=hours)
        
        if metric_type == 'system':
            cursor.execute('''
                SELECT timestamp, cpu_usage, memory_percent, network_bytes_sent, 
                       network_bytes_recv, disk_usage
                FROM system_metrics 
                WHERE timestamp > ? 
                ORDER BY timestamp
            ''', (since_time.isoformat(),)) # Use isoformat for DATETIME comparison
            
            data = cursor.fetchall()
            result = {
                'timestamps': [row[0] for row in data],
                'cpu_usage': [row[1] for row in data],
                'memory_percent': [row[2] for row in data],
                'network_sent': [row[3] for row in data],
                'network_recv': [row[4] for row in data],
                'disk_usage': [row[5] for row in data]
            }
        
        elif metric_type == 'flows':
            # This query aggregates flow_count by dpid and timestamp
            # It assumes you want total flows per dpid over time
            cursor.execute('''
                SELECT timestamp, dpid, SUM(flow_count) as total_flow_count
                FROM flow_stats 
                WHERE timestamp > ? 
                GROUP BY timestamp, dpid
                ORDER BY timestamp, dpid
            ''', (since_time.isoformat(),)) # Use isoformat for DATETIME comparison
            
            data = cursor.fetchall()
            result = defaultdict(list)
            for row in data:
                dpid = row[1]
                result[dpid].append({
                    'timestamp': row[0],
                    'flow_count': row[2]
                })
        
        logger.info(f"Historical data fetched for type '{metric_type}' for last {hours} hours.")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting historical data: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/network_events')
def get_network_events():
    """Get recent network events"""
    conn = None
    try:
        limit = request.args.get('limit', 50, type=int)
        severity = request.args.get('severity', None)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        query = 'SELECT * FROM network_events'
        params = []
        
        if severity:
            query += ' WHERE severity = ?'
            params.append(severity)
        
        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        events = cursor.fetchall()
        
        result = []
        for event in events:
            result.append({
                'id': event[0],
                'timestamp': event[1],
                'event_type': event[2],
                'dpid': event[3],
                'description': event[4],
                'severity': event[5]
            })
        
        logger.info(f"Fetched {len(result)} network events.")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting network events: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/analytics/summary')
def get_analytics_summary():
    """Get network analytics summary"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get system metrics summary for last 24 hours
        cursor.execute('''
            SELECT AVG(cpu_usage), MAX(cpu_usage), AVG(memory_percent), MAX(memory_percent)
            FROM system_metrics 
            WHERE timestamp > datetime('now', '-24 hours')
        ''')
        system_summary = cursor.fetchone()
        
        # Get network events count by severity
        cursor.execute('''
            SELECT severity, COUNT(*) 
            FROM network_events 
            WHERE timestamp > datetime('now', '-24 hours')
            GROUP BY severity
        ''')
        events_by_severity = dict(cursor.fetchall())
        
        # Get flow statistics trends
        # Note: This aggregates flow_count per dpid over the last 24 hours.
        # If you need a more granular trend (e.g., flow count at different times),
        # the historical_data endpoint is more appropriate.
        cursor.execute('''
            SELECT dpid, AVG(flow_count) as avg_flows, SUM(packet_count) as total_packets, SUM(byte_count) as total_bytes
            FROM flow_stats 
            WHERE timestamp > datetime('now', '-24 hours')
            GROUP BY dpid
        ''')
        flow_summary = cursor.fetchall()
        
        
        result = {
            'system_summary': {
                'avg_cpu': system_summary[0] or 0,
                'max_cpu': system_summary[1] or 0,
                'avg_memory': system_summary[2] or 0,
                'max_memory': system_summary[3] or 0
            },
            'events_summary': events_by_severity,
            'flow_summary': [
                {
                    'dpid': row[0],
                    'avg_flows': row[1] or 0,
                    'total_packets': row[2] or 0,
                    'total_bytes': row[3] or 0
                }
                for row in flow_summary
            ],
            'network_health': calculate_network_health_from_db()
        }
        
        logger.info("Analytics summary fetched.")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting analytics summary: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

def calculate_network_health_from_db():
    """Calculate network health from database"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Count recent errors
        cursor.execute('''
            SELECT COUNT(*) FROM network_events 
            WHERE severity IN ('error', 'warning') 
            AND timestamp > datetime('now', '-1 hour')
        ''')
        recent_errors = cursor.fetchone()[0]
        
        # Get system metrics health
        cursor.execute('''
            SELECT AVG(cpu_usage), AVG(memory_percent) 
            FROM system_metrics 
            WHERE timestamp > datetime('now', '-10 minutes')
        ''')
        system_health = cursor.fetchone()
        
        
        health_score = 100
        health_score -= recent_errors * 5  # Reduce 5 points per error
        
        if system_health[0] is not None and system_health[0] > 80:
            health_score -= 10
        if system_health[1] is not None and system_health[1] > 90:
            health_score -= 15
        
        logger.info(f"Network health calculated: {health_score}")
        return max(0, min(100, health_score))
        
    except Exception as e:
        logger.error(f"Error calculating network health from DB: {e}")
        return 50  # Default moderate health score
    finally:
        if conn:
            conn.close()

# WebSocket events for real-time updates
@socketio.on('connect')
def handle_connect():
    logger.info('Client connected to WebSocket')
    emit('status', {'msg': 'Connected to SDN Dashboard'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected from WebSocket')

def background_monitoring():
    """Background thread for continuous monitoring and alerting"""
    while True:
        try:
            # Monitor system metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_percent = psutil.virtual_memory().percent
            
            if cpu_percent > 50:
                store_network_event('high_cpu', '', f'High CPU usage: {cpu_percent:.1f}%', 'warning')
            
            if memory_percent > 50:
                store_network_event('high_memory', '', f'High memory usage: {memory_percent:.1f}%', 'warning')
            
            # Check Ryu connectivity and basic stats regularly
            try:
                response = requests.get(f"{RYU_API_URL}/v1.0/topology/switches", timeout=5)
                if response.status_code == 200:
                    switches = response.json()
                    if not switches:
                        store_network_event('ryu_no_switches', '', 'Ryu bağlı anahtar bulamıyor.', 'info')
                    logger.info(f"Ryu API reachable. Connected switches: {len(switches)}")
                else:
                    store_network_event('ryu_api_status_error', '', f'Ryu API beklenmeyen durum kodu döndürdü: {response.status_code}', 'error')
            except requests.exceptions.ConnectionError:
                store_network_event('ryu_connection_lost', '', 'Ryu kontrolcü bağlantısı kesildi.', 'error')
            except requests.exceptions.Timeout:
                store_network_event('ryu_timeout', '', 'Ryu API yanıt vermiyor (zaman aşımı).', 'error')
            except Exception as e:
                store_network_event('ryu_check_error', '', f'Ryu bağlantı kontrol hatası: {e}', 'error')
            
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            logger.error(f"Background monitoring general error: {e}")
            time.sleep(60)  # Wait longer on error

if __name__ == '__main__':
    # Initialize database
    init_database()
    
    # Start background monitoring thread
    monitoring_thread = threading.Thread(target=background_monitoring, daemon=True)
    monitoring_thread.start()
    
    # Start the Flask-SocketIO server
    logger.info("Starting Flask-SocketIO server...")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import subprocess
import os
import json
import zipfile
import shutil
import signal
import sys
import time

app = Flask(__name__)
CORS(app)

# ============================================
# CONFIG
# ============================================
SERVERS_FILE = 'servers.json'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Store running processes
running_processes = {}

# ============================================
# HELPERS
# ============================================
def load_servers():
    if os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_servers(servers):
    with open(SERVERS_FILE, 'w') as f:
        json.dump(servers, f, indent=2)

def get_server(id):
    servers = load_servers()
    for s in servers:
        if s['id'] == id:
            return s
    return None

# ============================================
# ROUTES
# ============================================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/console')
def console():
    return render_template('server-console.html')

# ============================================
# API - GET SERVERS
# ============================================
@app.route('/api/servers')
def api_servers():
    servers = load_servers()
    # Add running status
    for s in servers:
        s['is_running'] = s['id'] in running_processes
    return jsonify(servers)

# ============================================
# API - START SERVER
# ============================================
@app.route('/api/start/<int:server_id>', methods=['POST'])
def api_start(server_id):
    server = get_server(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404

    # Check if already running
    if server_id in running_processes:
        return jsonify({'error': 'Server already running'}), 400

    # Get file path
    file_path = os.path.join(UPLOAD_FOLDER, str(server_id), server['file'])
    
    if not os.path.exists(file_path):
        return jsonify({'error': f'File not found: {server["file"]}'}), 404

    try:
        # Start process
        if server['runtime'] == 'python':
            process = subprocess.Popen(
                ['python3', file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.path.join(UPLOAD_FOLDER, str(server_id))
            )
        elif server['runtime'] == 'node':
            process = subprocess.Popen(
                ['node', file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.path.join(UPLOAD_FOLDER, str(server_id))
            )
        else:
            return jsonify({'error': f'Unsupported runtime: {server["runtime"]}'}), 400

        running_processes[server_id] = {
            'process': process,
            'server': server,
            'start_time': time.time()
        }

        # Update server status
        servers = load_servers()
        for s in servers:
            if s['id'] == server_id:
                s['status'] = 'running'
                break
        save_servers(servers)

        return jsonify({'success': True, 'message': f'Server {server["name"]} started!'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================
# API - STOP SERVER
# ============================================
@app.route('/api/stop/<int:server_id>', methods=['POST'])
def api_stop(server_id):
    if server_id not in running_processes:
        return jsonify({'error': 'Server not running'}), 400

    try:
        process_info = running_processes[server_id]
        process = process_info['process']
        
        # Try graceful shutdown
        process.terminate()
        time.sleep(1)
        
        # Force kill if still running
        if process.poll() is None:
            process.kill()

        del running_processes[server_id]

        # Update server status
        servers = load_servers()
        for s in servers:
            if s['id'] == server_id:
                s['status'] = 'stopped'
                break
        save_servers(servers)

        return jsonify({'success': True, 'message': 'Server stopped!'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================
# API - RESTART SERVER
# ============================================
@app.route('/api/restart/<int:server_id>', methods=['POST'])
def api_restart(server_id):
    # Stop first
    if server_id in running_processes:
        try:
            process_info = running_processes[server_id]
            process = process_info['process']
            process.terminate()
            time.sleep(1)
            if process.poll() is None:
                process.kill()
            del running_processes[server_id]
        except:
            pass

    # Then start
    return api_start(server_id)

# ============================================
# API - GET LOGS
# ============================================
@app.route('/api/logs/<int:server_id>')
def api_logs(server_id):
    if server_id not in running_processes:
        return jsonify({'logs': ['Server is not running']})

    try:
        process_info = running_processes[server_id]
        process = process_info['process']
        
        # Get stdout
        stdout, stderr = process.communicate(timeout=0.1)
        
        logs = []
        if stdout:
            for line in stdout.split('\n'):
                if line.strip():
                    logs.append({'type': 'output', 'line': line})
        if stderr:
            for line in stderr.split('\n'):
                if line.strip():
                    logs.append({'type': 'error', 'line': line})
        
        return jsonify({'logs': logs})

    except subprocess.TimeoutExpired:
        # Process still running, get partial output
        try:
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            logs = []
            if stdout:
                for line in stdout.split('\n'):
                    if line.strip():
                        logs.append({'type': 'output', 'line': line})
            if stderr:
                for line in stderr.split('\n'):
                    if line.strip():
                        logs.append({'type': 'error', 'line': line})
            return jsonify({'logs': logs})
        except:
            return jsonify({'logs': ['No output available']})

    except Exception as e:
        return jsonify({'logs': [f'Error reading logs: {str(e)}']})

# ============================================
# API - UPLOAD FILE
# ============================================
@app.route('/api/upload/<int:server_id>', methods=['POST'])
def api_upload(server_id):
    server = get_server(server_id)
    if not server:
        return jsonify({'error': 'Server not found'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Create server folder
    server_folder = os.path.join(UPLOAD_FOLDER, str(server_id))
    os.makedirs(server_folder, exist_ok=True)

    # Save file
    file_path = os.path.join(server_folder, file.filename)
    file.save(file_path)

    # If ZIP, extract it
    if file.filename.endswith('.zip'):
        try:
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(server_folder)
            
            # Find main file
            for root, dirs, files in os.walk(server_folder):
                for f in files:
                    if f.endswith('.py') or f.endswith('.js'):
                        server['file'] = f
                        break
                if server.get('file'):
                    break

            # Update server config
            servers = load_servers()
            for s in servers:
                if s['id'] == server_id:
                    s['file'] = server.get('file', file.filename.replace('.zip', '.py'))
                    break
            save_servers(servers)

            return jsonify({
                'success': True,
                'message': 'ZIP uploaded and extracted!',
                'main_file': server.get('file')
            })

        except zipfile.BadZipFile:
            return jsonify({'error': 'Invalid ZIP file'}), 400

    return jsonify({
        'success': True,
        'message': f'File {file.filename} uploaded!',
        'file': file.filename
    })

# ============================================
# API - DELETE SERVER
# ============================================
@app.route('/api/delete/<int:server_id>', methods=['DELETE'])
def api_delete(server_id):
    # Stop if running
    if server_id in running_processes:
        try:
            process_info = running_processes[server_id]
            process = process_info['process']
            process.terminate()
            time.sleep(1)
            if process.poll() is None:
                process.kill()
            del running_processes[server_id]
        except:
            pass

    # Delete folder
    server_folder = os.path.join(UPLOAD_FOLDER, str(server_id))
    if os.path.exists(server_folder):
        shutil.rmtree(server_folder)

    # Remove from servers.json
    servers = load_servers()
    servers = [s for s in servers if s['id'] != server_id]
    save_servers(servers)

    return jsonify({'success': True, 'message': 'Server deleted!'})

# ============================================
# API - CREATE SERVER
# ============================================
@app.route('/api/create', methods=['POST'])
def api_create():
    data = request.get_json()
    name = data.get('name')
    runtime = data.get('runtime', 'python')
    file = data.get('file', 'bot.py')

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    servers = load_servers()
    new_id = max([s['id'] for s in servers], default=0) + 1

    new_server = {
        'id': new_id,
        'name': name,
        'runtime': runtime,
        'file': file,
        'status': 'stopped'
    }

    servers.append(new_server)
    save_servers(servers)

    # Create folder
    os.makedirs(os.path.join(UPLOAD_FOLDER, str(new_id)), exist_ok=True)

    return jsonify({'success': True, 'server': new_server})

# ============================================
# RUN
# ============================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
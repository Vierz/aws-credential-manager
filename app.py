from flask import Flask, render_template, request, redirect, flash
import configparser
import os
import subprocess
import json
import yaml

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Necesario para flash messages

# Rutas a los archivos
CREDENTIALS_FILE = os.path.expanduser('~/.aws/credentials')
KUBE_CONFIG_FILE = os.path.expanduser('~/.kube/config')

def read_credentials():
    config = configparser.ConfigParser()
    credentials = {}
    
    try:
        if os.path.exists(CREDENTIALS_FILE):
            config.read(CREDENTIALS_FILE)
            for section in config.sections():
                credentials[section] = {
                    'aws_access_key_id': config[section].get('aws_access_key_id', ''),
                    'aws_secret_access_key': config[section].get('aws_secret_access_key', ''),
                    'aws_session_token': config[section].get('aws_session_token', '')
                }
        return credentials
    except Exception as e:
        flash(f'Error al leer el archivo de credenciales: {str(e)}', 'error')
        return {}

def read_kubeconfig():
    kubeconfig = {}
    
    try:
        if os.path.exists(KUBE_CONFIG_FILE):
            with open(KUBE_CONFIG_FILE, 'r') as file:
                config = yaml.safe_load(file)
                if config and 'contexts' in config:
                    for context in config.get('contexts', []):
                        context_name = context['name']
                        cluster = context['context']['cluster']
                        # Buscar perfiles en el context_name (formato: profile@cluster)
                        for profile in read_credentials().keys():
                            if context_name.startswith(f"{profile}@"):
                                if profile not in kubeconfig:
                                    kubeconfig[profile] = []
                                # Usar el ARN completo del clúster
                                if cluster not in kubeconfig[profile]:
                                    kubeconfig[profile].append(cluster)
        return kubeconfig
    except Exception as e:
        flash(f'Error al leer el archivo kubeconfig: {str(e)}', 'error')
        return {}

def save_credentials(credentials):
    config = configparser.ConfigParser()
    
    try:
        for profile, creds in credentials.items():
            config[profile] = {
                key: value for key, value in creds.items() if value
            }
        
        os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
        with open(CREDENTIALS_FILE, 'w') as configfile:
            config.write(configfile)
        return True
    except Exception as e:
        flash(f'Error al guardar el archivo de credenciales: {str(e)}', 'error')
        return False

@app.route('/')
def index():
    credentials = read_credentials()
    kubeconfig = read_kubeconfig()
    return render_template('index.html', credentials=credentials, kubeconfig=kubeconfig)

@app.route('/delete/<profile>', methods=['POST'])
def delete_profile(profile):
    credentials = read_credentials()
    
    if profile in credentials:
        del credentials[profile]
        if save_credentials(credentials):
            flash(f'Perfil {profile} eliminado exitosamente.', 'success')
        else:
            flash(f'Error al eliminar el perfil {profile}.', 'error')
    else:
        flash(f'El perfil {profile} no existe.', 'error')
    
    return redirect('/')

@app.route('/edit/<profile>', methods=['GET', 'POST'])
def edit_profile(profile):
    credentials = read_credentials()
    
    if request.method == 'POST':
        profile_content = request.form.get('profile_content', '')
        try:
            # Parsear el contenido del textarea como INI
            config = configparser.ConfigParser()
            config.read_string(f'[{profile}]\n{profile_content}')
            
            new_profile_name = request.form.get('profile', profile)
            creds = {
                'aws_access_key_id': config[profile].get('aws_access_key_id', ''),
                'aws_secret_access_key': config[profile].get('aws_secret_access_key', ''),
                'aws_session_token': config[profile].get('aws_session_token', '')
            }
            
            if profile in credentials:
                del credentials[profile]
            credentials[new_profile_name] = creds
            
            if save_credentials(credentials):
                flash(f'Perfil {new_profile_name} guardado exitosamente.', 'success')
                return redirect('/')
            else:
                flash(f'Error al guardar el perfil {new_profile_name}.', 'error')
                return render_template('edit.html', profile=profile, profile_content=profile_content)
        
        except Exception as e:
            flash(f'Error al parsear el contenido del perfil: {str(e)}', 'error')
            return render_template('edit.html', profile=profile, profile_content=profile_content)
    
    creds = credentials.get(profile, {
        'aws_access_key_id': '',
        'aws_secret_access_key': '',
        'aws_session_token': ''
    })
    # Formatear el contenido del perfil para el textarea
    profile_content = '\n'.join(
        f'{key}={value}' for key, value in creds.items() if value
    )
    return render_template('edit.html', profile=profile, profile_content=profile_content)

@app.route('/add', methods=['POST'])
def add_profile():
    credentials = read_credentials()
    
    profile_content = request.form.get('profile_content', '').strip()
    
    if not profile_content:
        flash('El contenido del perfil no puede estar vacío.', 'error')
        return redirect('/')
    
    try:
        # Parsear el contenido del textarea como INI
        config = configparser.ConfigParser()
        config.read_string(profile_content)
        
        # Obtener el nombre del perfil (debe haber solo una sección)
        if len(config.sections()) != 1:
            flash('El contenido debe contener exactamente un perfil (una sección [profile_name]).', 'error')
            return redirect('/')
        
        profile = config.sections()[0]
        if not profile:
            flash('El nombre del perfil no puede estar vacío.', 'error')
            return redirect('/')
        
        if profile in credentials:
            flash(f'El perfil {profile} ya existe.', 'error')
            return redirect('/')
        
        creds = {
            'aws_access_key_id': config[profile].get('aws_access_key_id', ''),
            'aws_secret_access_key': config[profile].get('aws_secret_access_key', ''),
            'aws_session_token': config[profile].get('aws_session_token', '')
        }
        
        credentials[profile] = creds
        if save_credentials(credentials):
            flash(f'Perfil {profile} añadido exitosamente.', 'success')
        else:
            flash(f'Error al añadir el perfil {profile}.', 'error')
    
    except Exception as e:
        flash(f'Error al parsear el contenido del perfil: {str(e)}', 'error')
    
    return redirect('/')

@app.route('/create-kubeconfig/<profile>', methods=['GET'])
def create_kubeconfig(profile):
    credentials = read_credentials()
    
    if profile not in credentials:
        flash(f'El perfil {profile} no existe.', 'error')
        return redirect('/')
    
    try:
        # Ejecutar aws eks list-clusters
        result = subprocess.run(
            ['aws', 'eks', 'list-clusters', '--region', 'us-east-1', '--profile', profile, '--output', 'json'],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            flash(f'Error al listar clústeres: {result.stderr}', 'error')
            return redirect('/')
        
        clusters = json.loads(result.stdout).get('clusters', [])
        if not clusters:
            flash('No se encontraron clústeres EKS en la cuenta.', 'error')
            return redirect('/')
        
        return render_template('select_cluster.html', profile=profile, clusters=clusters)
    
    except Exception as e:
        flash(f'Error al listar clústeres: {str(e)}', 'error')
        return redirect('/')

@app.route('/run-kubeconfig/<profile>', methods=['POST'])
def run_kubeconfig(profile):
    credentials = read_credentials()
    
    if profile not in credentials:
        flash(f'El perfil {profile} no existe.', 'error')
        return redirect('/')
    
    cluster = request.form.get('cluster')
    if not cluster:
        flash('Debes seleccionar un clúster.', 'error')
        return redirect(f'/create-kubeconfig/{profile}')
    
    try:
        # Ejecutar aws eks update-kubeconfig con un alias único
        alias = f"{profile}@{cluster}"
        cmd = [
            'aws', 'eks', 'update-kubeconfig',
            '--region', 'us-east-1',
            '--name', cluster,
            '--profile', profile,
            '--alias', alias
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            flash(f'Configuración de kubeconfig creada para el clúster {cluster} con el perfil {profile}.', 'success')
        else:
            flash(f'Error al crear kubeconfig: {result.stderr}', 'error')
        
        return redirect('/')
    
    except Exception as e:
        flash(f'Error al ejecutar update-kubeconfig: {str(e)}', 'error')
        return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
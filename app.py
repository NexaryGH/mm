import os
import uuid
import json
import logging
from typing import List
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_file
)
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import github
from github import Github
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PhotoUploadConfig:
    """Configuration class for photo upload settings."""
    SHARED_PHOTOS_FILE = 'shared_photos.json'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_PHOTOS_PER_USER = 15
    MAX_FILE_SIZE = 16 * 1024 * 1024  # 16MB

    # GitHub Repository Configuration
    GITHUB_REPO_OWNER = os.getenv('GITHUB_REPO_OWNER')
    GITHUB_REPO_NAME = os.getenv('GITHUB_REPO_NAME')
    GITHUB_ACCESS_TOKEN = os.getenv('GITHUB_ACCESS_TOKEN')

    # Google Drive Configuration
    GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.urandom(24)
    app.config['MAX_CONTENT_LENGTH'] = PhotoUploadConfig.MAX_FILE_SIZE
    return app

app = create_app()

def read_shared_photos() -> List[dict]:
    """Read shared photos metadata from GitHub repository."""
    try:
        g = Github(PhotoUploadConfig.GITHUB_ACCESS_TOKEN)
        repo = g.get_repo(f"{PhotoUploadConfig.GITHUB_REPO_OWNER}/{PhotoUploadConfig.GITHUB_REPO_NAME}")
        try:
            file_contents = repo.get_contents(PhotoUploadConfig.SHARED_PHOTOS_FILE)
            return json.loads(file_contents.decoded_content)
        except github.UnknownObjectException:
            return []
    except Exception as e:
        logger.error(f"Error reading shared photos: {str(e)}")
        return []

def write_shared_photos(photos: List[dict]):
    """Write shared photos metadata to GitHub repository."""
    try:
        g = Github(PhotoUploadConfig.GITHUB_ACCESS_TOKEN)
        repo = g.get_repo(f"{PhotoUploadConfig.GITHUB_REPO_OWNER}/{PhotoUploadConfig.GITHUB_REPO_NAME}")
        file_contents = json.dumps(photos, indent=2)
        try:
            existing_file = repo.get_contents(PhotoUploadConfig.SHARED_PHOTOS_FILE)
            repo.update_file(
                path=PhotoUploadConfig.SHARED_PHOTOS_FILE,
                message="Update shared photos metadata",
                content=file_contents,
                sha=existing_file.sha
            )
        except github.UnknownObjectException:
            repo.create_file(
                path=PhotoUploadConfig.SHARED_PHOTOS_FILE,
                message="Create shared photos metadata",
                content=file_contents
            )
    except Exception as e:
        logger.error(f"Error writing shared photos: {str(e)}")

def upload_to_google_drive(file, filename):
    """Upload file to Google Drive."""
    try:
        creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/drive.file'])
        service = build('drive', 'v3', credentials=creds)

        file_metadata = {
            'name': filename,
            'parents': [PhotoUploadConfig.GOOGLE_DRIVE_FOLDER_ID]
        }
        media = MediaIoBaseUpload(io.BytesIO(file.read()), mimetype=file.content_type, resumable=True)

        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        return uploaded_file.get('id')
    except Exception as e:
        logger.error(f"Google Drive upload error: {str(e)}")
        raise

def allowed_file(filename: str) -> bool:
    """Check if the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in PhotoUploadConfig.ALLOWED_EXTENSIONS

def generate_unique_filename(filename: str) -> str:
    """Generate a unique filename while preserving the original file extension."""
    ext = filename.rsplit('.', 1)[1].lower()
    return f"{uuid.uuid4()}.{ext}"

@app.route('/')
def index():
    """Render the main index page with uploaded photos."""
    if 'photos' not in session:
        session['photos'] = []
    
    shared_photos = read_shared_photos()
    filtered_photos = [photo for photo in shared_photos if photo['uploader'] == session.get('username', 'Invitado')]
    
    photos_remaining = max(PhotoUploadConfig.MAX_PHOTOS_PER_USER - len(session['photos']), 0)
    
    return render_template(
        'index.html',
        photos=session['photos'],
        shared_photos=filtered_photos,
        photos_remaining=photos_remaining
    )

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload process."""
    if 'photos' not in session:
        session['photos'] = []
    
    if len(session['photos']) >= PhotoUploadConfig.MAX_PHOTOS_PER_USER:
        flash(f'Máximo límite de {PhotoUploadConfig.MAX_PHOTOS_PER_USER} fotos alcanzado', 'error')
        return redirect(url_for('index'))
    
    if 'file' not in request.files:
        flash('No se seleccionó ningún archivo', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    
    if file.filename == '':
        flash('No se seleccionó ningún archivo', 'error')
        return redirect(url_for('index'))
    
    if file and allowed_file(file.filename):
        try:
            secure_fn = secure_filename(file.filename)
            unique_filename = generate_unique_filename(secure_fn)
            drive_file_id = upload_to_google_drive(file, unique_filename)

            session['photos'].append(unique_filename)
            shared_photos = read_shared_photos()
            shared_photos.append({
                'filename': unique_filename,
                'drive_file_id': drive_file_id,
                'uploader': session.get('username', 'Invitado'),
                'timestamp': str(uuid.uuid4())
            })
            write_shared_photos(shared_photos)

            logger.info(f"File uploaded successfully: {unique_filename}")
            flash('¡Archivo subido exitosamente!', 'success')
            return redirect(url_for('index'))
        
        except Exception as e:
            logger.error(f"Error uploading file: {str(e)}")
            flash(f'Error al subir el archivo: {str(e)}', 'error')
            return redirect(url_for('index'))
    
    else:
        flash('Tipo de archivo inválido.', 'error')
        return redirect(url_for('index'))

@app.route('/delete/<filename>', methods=['POST'])
def delete_photo(filename: str):
    """Eliminar una foto compartida que subió el usuario."""
    shared_photos = read_shared_photos()
    current_username = session.get('username', 'Invitado')

    photo_to_delete = next(
        (photo for photo in shared_photos if photo['filename'] == filename and photo['uploader'] == current_username), 
        None
    )

    if photo_to_delete:
        try:
            creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/drive.file'])
            service = build('drive', 'v3', credentials=creds)
            service.files().delete(fileId=photo_to_delete['drive_file_id']).execute()
        except Exception as e:
            logger.error(f"Error deleting from Google Drive: {str(e)}")

        updated_photos = [photo for photo in shared_photos
                          if photo['filename'] != filename or photo['uploader'] != current_username]
        
        write_shared_photos(updated_photos)
        logger.info(f"Foto eliminada: {filename} por {current_username}")
        flash('¡Foto eliminada exitosamente!', 'success')
    else:
        flash('No tienes permisos para eliminar esta foto.', 'error')

    return redirect(url_for('index'))

@app.route('/view/<filename>')
def view_photo(filename: str):
    """View a photo from Google Drive."""
    try:
        shared_photos = read_shared_photos()
        photo = next((p for p in shared_photos if p['filename'] == filename), None)
        
        if not photo:
            flash('Foto no encontrada', 'error')
            return redirect(url_for('index'))
        
        creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/drive.file'])
        service = build('drive', 'v3', credentials=creds)
        
        file_content = service.files().get_media(fileId=photo['drive_file_id']).execute()
        file_obj = io.BytesIO(file_content)
        file_obj.seek(0)
        
        return send_file(file_obj, download_name=filename)
    except Exception as e:
        logger.error(f"Error viewing photo: {str(e)}")
        flash('Error al ver la foto', 'error')
        return redirect(url_for('index'))

@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(error):
    """Handle file size exceeding limit.""" 
    flash(f'Tamaño de archivo excedido. Máximo {PhotoUploadConfig.MAX_FILE_SIZE // (1024*1024)} MB', 'error')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
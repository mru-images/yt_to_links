from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import requests
from io import BytesIO
import base64
import tempfile

app = FastAPI()

SONGS_FOLDER = "songs"
IMGS_FOLDER = "imgs"

# 🔐 Read from environment variables
PCLOUD_USERNAME = os.getenv("PCLOUD_USERNAME")
PCLOUD_PASSWORD = os.getenv("PCLOUD_PASSWORD")
YOUTUBE_COOKIES_BASE64 = os.getenv("YOUTUBE_COOKIES")  # base64 encoded cookies

# Authenticate with pCloud
def get_auth_token():
    res = requests.get('https://api.pcloud.com/login', params={
        'getauth': 1,
        'username': PCLOUD_USERNAME,
        'password': PCLOUD_PASSWORD
    }).json()
    if not res.get('auth'):
        raise Exception("pCloud auth failed: " + str(res))
    return res['auth']

# Create or get folder ID
def get_or_create_folder(auth_token, folder_name):
    res = requests.get('https://api.pcloud.com/listfolder', params={
        'auth': auth_token,
        'folderid': 0,
        'recursive': 1
    }).json()
    for item in res.get('metadata', {}).get('contents', []):
        if item['isfolder'] and item['name'] == folder_name:
            return item['folderid']
    create = requests.get('https://api.pcloud.com/createfolder', params={
        'auth': auth_token,
        'name': folder_name,
        'folderid': 0
    }).json()
    return create['metadata']['folderid']

# Download audio and get thumbnail URL
def download_audio_and_thumbnail(video_url: str):
    buffer = BytesIO()
    temp_id = str(uuid.uuid4())
    filename = f"{temp_id}.mp3"

    if not YOUTUBE_COOKIES_BASE64:
        raise Exception("Missing YOUTUBE_COOKIES env var")

    with tempfile.NamedTemporaryFile(delete=False, mode='w+', suffix=".txt") as cookie_file:
        cookie_text = base64.b64decode(YOUTUBE_COOKIES_BASE64).decode()
        cookie_file.write(cookie_text)
        cookie_path = cookie_file.name

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f"{temp_id}.%(ext)s",
        'quiet': True,
        'cookiefile': cookie_path,
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        full_path = f"{temp_id}.mp3"
        thumbnail_url = info.get("thumbnail", None)

    with open(full_path, 'rb') as f:
        buffer.write(f.read())
        buffer.seek(0)

    os.remove(full_path)
    os.remove(cookie_path)
    return buffer, filename, thumbnail_url

# Download thumbnail image
def download_thumbnail(thumbnail_url):
    res = requests.get(thumbnail_url)
    if res.status_code == 200:
        buffer = BytesIO(res.content)
        filename = f"{uuid.uuid4()}.jpg"
        return buffer, filename
    raise Exception("Failed to download thumbnail")

# Upload to pCloud
def upload_to_pcloud(auth_token, folder_id, file_buffer, filename):
    file_buffer.seek(0)
    res = requests.post('https://api.pcloud.com/uploadfile', params={
        'auth': auth_token,
        'folderid': folder_id
    }, files={'file': (filename, file_buffer)}).json()
    fileid = res['metadata'][0]['fileid']
    link_res = requests.get('https://api.pcloud.com/getfilelink', params={
        'auth': auth_token,
        'fileid': fileid
    }).json()
    return link_res['hosts'][0] + link_res['path']

# Health check
@app.get("/")
def home():
    return {"message": "YouTube to pCloud Uploader is running on Render!"}

# Upload endpoint
@app.get("/upload")
def upload(link: str = Query(..., description="YouTube video URL")):
    try:
        auth_token = get_auth_token()
        songs_folder_id = get_or_create_folder(auth_token, SONGS_FOLDER)
        imgs_folder_id = get_or_create_folder(auth_token, IMGS_FOLDER)

        audio_buffer, audio_filename, thumbnail_url = download_audio_and_thumbnail(link)
        audio_link = upload_to_pcloud(auth_token, songs_folder_id, audio_buffer, audio_filename)
        audio_buffer.close()

        if not thumbnail_url:
            raise Exception("No thumbnail found.")

        thumb_buffer, thumb_filename = download_thumbnail(thumbnail_url)
        thumb_link = upload_to_pcloud(auth_token, imgs_folder_id, thumb_buffer, thumb_filename)
        thumb_buffer.close()

        return JSONResponse(content={
            "mp3_link": audio_link,
            "thumbnail_link": thumb_link
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

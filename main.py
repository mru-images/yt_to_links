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

PCLOUD_AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
YOUTUBE_COOKIES_BASE64 = os.getenv("YOUTUBE_COOKIES")

# Get auth token
def get_auth_token():
    if not PCLOUD_AUTH_TOKEN:
        raise Exception("Missing PCLOUD_AUTH_TOKEN environment variable")
    return PCLOUD_AUTH_TOKEN

# Create or fetch folder ID by name
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

# Download audio & extract thumbnail URL
def download_audio_and_thumbnail(video_url: str):
    buffer = BytesIO()
    temp_id = str(uuid.uuid4())
    filename = f"{temp_id}.mp3"

    if not YOUTUBE_COOKIES_BASE64:
        raise Exception("Missing YOUTUBE_COOKIES environment variable")

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

# Upload to pCloud and get permanent public link
def upload_to_pcloud_and_get_public_link(auth_token, folder_id, file_buffer, filename):
    file_buffer.seek(0)
    upload_res = requests.post('https://api.pcloud.com/uploadfile', params={
        'auth': auth_token,
        'folderid': folder_id
    }, files={'file': (filename, file_buffer)}).json()

    fileid = upload_res['metadata'][0]['fileid']

    public_res = requests.get('https://api.pcloud.com/getpublink', params={
        'auth': auth_token,
        'fileid': fileid
    }).json()

    if 'metadata' in public_res and 'link' in public_res['metadata']:
        return public_res['metadata']['link']

    raise Exception("Failed to create public link")

@app.get("/")
def home():
    return {"message": "YouTube to pCloud Uploader is running!"}

@app.get("/upload")
def upload(link: str = Query(..., description="YouTube video URL")):
    try:
        auth_token = get_auth_token()
        songs_folder_id = get_or_create_folder(auth_token, SONGS_FOLDER)
        imgs_folder_id = get_or_create_folder(auth_token, IMGS_FOLDER)

        # Download MP3 and get thumbnail URL
        audio_buffer, audio_filename, thumbnail_url = download_audio_and_thumbnail(link)
        mp3_link = upload_to_pcloud_and_get_public_link(auth_token, songs_folder_id, audio_buffer, audio_filename)
        audio_buffer.close()

        if not thumbnail_url:
            raise Exception("No thumbnail found in video.")

        thumb_buffer, thumb_filename = download_thumbnail(thumbnail_url)
        thumb_link = upload_to_pcloud_and_get_public_link(auth_token, imgs_folder_id, thumb_buffer, thumb_filename)
        thumb_buffer.close()

        return JSONResponse(content={
            "mp3_link": mp3_link,
            "thumbnail_link": thumb_link
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

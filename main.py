from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import requests
from io import BytesIO
import base64
import tempfile
import re

app = FastAPI()

SONGS_FOLDER = "songs"
IMGS_FOLDER = "imgs"

PCLOUD_AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
YOUTUBE_COOKIES_BASE64 = os.getenv("YOUTUBE_COOKIES")

def get_auth_token():
    if not PCLOUD_AUTH_TOKEN:
        raise Exception("Missing PCLOUD_AUTH_TOKEN environment variable")
    return PCLOUD_AUTH_TOKEN

def get_or_create_folder(auth_token, folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={
        "auth": auth_token,
        "folderid": 0
    }).json()
    for item in res.get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={
        "auth": auth_token,
        "name": folder_name,
        "folderid": 0
    }).json()
    if res.get("result") != 0:
        raise Exception(f"Failed to create folder: {res}")
    return res["metadata"]["folderid"]

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def download_audio_and_thumbnail(video_url: str):
    buffer = BytesIO()
    if not YOUTUBE_COOKIES_BASE64:
        raise Exception("Missing YOUTUBE_COOKIES environment variable")

    with tempfile.NamedTemporaryFile(delete=False, mode='w+', suffix=".txt") as cookie_file:
        cookie_text = base64.b64decode(YOUTUBE_COOKIES_BASE64).decode()
        cookie_file.write(cookie_text)
        cookie_path = cookie_file.name

    temp_id = str(uuid.uuid4())
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
        title = sanitize_filename(info.get("title", temp_id))
        thumbnail_url = info.get("thumbnail")
        mp3_path = f"{temp_id}.mp3"
        mp3_filename = f"{title}.mp3"

    with open(mp3_path, 'rb') as f:
        buffer.write(f.read())
        buffer.seek(0)

    os.remove(mp3_path)
    os.remove(cookie_path)
    return buffer, mp3_filename, thumbnail_url

def download_thumbnail(thumbnail_url):
    res = requests.get(thumbnail_url)
    if res.status_code == 200:
        buffer = BytesIO(res.content)
        filename = f"{uuid.uuid4()}.jpg"
        return buffer, filename
    raise Exception("Failed to download thumbnail")

def upload_to_pcloud(auth_token, folder_id, file_buffer, filename):
    file_buffer.seek(0)
    res = requests.post("https://api.pcloud.com/uploadfile", params={
        "auth": auth_token,
        "folderid": folder_id
    }, files={"file": (filename, file_buffer)}).json()
    if res.get("result") != 0:
        raise Exception(f"Upload failed: {res}")
    metadata = res["metadata"][0]
    fileid = metadata["fileid"]
    return fileid, filename

def get_direct_link(auth_token, fileid):
    res = requests.get("https://api.pcloud.com/getfilelink", params={
        "auth": auth_token,
        "fileid": fileid
    }).json()
    if res.get("result") != 0:
        raise Exception(f"Failed to get direct link: {res}")
    return res["hosts"][0] + res["path"]

@app.get("/")
def home():
    return {"message": "YouTube to pCloud direct streaming uploader is running!"}

@app.get("/upload")
def upload(link: str = Query(..., description="YouTube video URL")):
    try:
        token = get_auth_token()
        songs_folder_id = get_or_create_folder(token, SONGS_FOLDER)
        imgs_folder_id = get_or_create_folder(token, IMGS_FOLDER)

        audio_buffer, audio_filename, thumbnail_url = download_audio_and_thumbnail(link)
        audio_fileid, _ = upload_to_pcloud(token, songs_folder_id, audio_buffer, audio_filename)
        audio_buffer.close()
        audio_stream_link = get_direct_link(token, audio_fileid)

        if not thumbnail_url:
            raise Exception("No thumbnail found.")
        thumb_buffer, thumb_filename = download_thumbnail(thumbnail_url)
        thumb_fileid, _ = upload_to_pcloud(token, imgs_folder_id, thumb_buffer, thumb_filename)
        thumb_buffer.close()
        thumb_stream_link = get_direct_link(token, thumb_fileid)

        return JSONResponse(content={
            "mp3_stream_link": audio_stream_link,
            "thumbnail_stream_link": thumb_stream_link,
            "file_name": audio_filename
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

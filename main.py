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

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_folder(auth_token, folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={
        "auth": auth_token, "folderid": 0
    }).json()
    for item in res.get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={
        "auth": auth_token, "name": folder_name, "folderid": 0
    }).json()
    return res["metadata"]["folderid"]

def download_audio_and_thumbnail(url):
    buffer = BytesIO()
    temp_id = str(uuid.uuid4())

    cookies_text = base64.b64decode(YOUTUBE_COOKIES_BASE64).decode()
    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix=".txt") as cfile:
        cfile.write(cookies_text)
        cookie_path = cfile.name

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{temp_id}.%(ext)s",
        "cookiefile": cookie_path,
        "quiet": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192"
        }]
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = sanitize_filename(info.get("title") or temp_id)
        thumb_url = info.get("thumbnail")
        mp3_path = f"{temp_id}.mp3"
        mp3_name = f"{title}.mp3"

    with open(mp3_path, 'rb') as f:
        buffer.write(f.read())
        buffer.seek(0)

    os.remove(mp3_path)
    os.remove(cookie_path)
    return buffer, mp3_name, thumb_url

def download_thumbnail(url):
    res = requests.get(url)
    if res.ok:
        return BytesIO(res.content), f"{uuid.uuid4()}.jpg"
    raise Exception("Thumbnail download failed")

def upload_to_pcloud(auth_token, folder_id, file_buffer, filename):
    file_buffer.seek(0)
    res = requests.post("https://api.pcloud.com/uploadfile", params={
        "auth": auth_token,
        "folderid": folder_id
    }, files={"file": (filename, file_buffer)}).json()
    if res["result"] != 0:
        raise Exception(f"Upload failed: {res}")
    fileid = res["metadata"][0]["fileid"]
    return fileid

def get_direct_link(auth_token, fileid):
    res = requests.get("https://api.pcloud.com/getfilelink", params={
        "auth": auth_token,
        "fileid": fileid
    }).json()
    if res["result"] != 0:
        raise Exception("Failed to get direct link")
    return res["hosts"][0] + res["path"]

@app.get("/")
def root():
    return {"message": "YouTube to pCloud public streaming uploader is online!"}

@app.get("/upload")
def upload(link: str = Query(...)):
    try:
        auth = PCLOUD_AUTH_TOKEN
        song_fid = get_folder(auth, SONGS_FOLDER)
        img_fid = get_folder(auth, IMGS_FOLDER)

        audio_buffer, mp3_name, thumb_url = download_audio_and_thumbnail(link)
        audio_fid = upload_to_pcloud(auth, song_fid, audio_buffer, mp3_name)
        audio_url = get_direct_link(auth, audio_fid)
        audio_buffer.close()

        if not thumb_url:
            raise Exception("No thumbnail found")
        thumb_buffer, thumb_name = download_thumbnail(thumb_url)
        thumb_fid = upload_to_pcloud(auth, img_fid, thumb_buffer, thumb_name)
        thumb_url = get_direct_link(auth, thumb_fid)
        thumb_buffer.close()

        return JSONResponse({
            "mp3": audio_url,
            "thumbnail": thumb_url,
            "file_name": mp3_name
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

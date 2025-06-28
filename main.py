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
AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
YOUTUBE_COOKIES_BASE64 = os.getenv("YOUTUBE_COOKIES")

def get_or_create_folder(auth_token, folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={
        "auth": auth_token,
        "folderid": 0,
        "recursive": 1
    }).json()
    for item in res.get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={
        "auth": auth_token,
        "name": folder_name,
        "folderid": 0
    }).json()
    return res["metadata"]["folderid"]

def download_audio_and_thumbnail(video_url):
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
        thumbnail_url = info.get("thumbnail")

    with open(full_path, 'rb') as f:
        buffer.write(f.read())
        buffer.seek(0)

    os.remove(full_path)
    os.remove(cookie_path)
    return buffer, filename, thumbnail_url

def download_thumbnail(thumbnail_url):
    res = requests.get(thumbnail_url)
    if res.status_code == 200:
        buffer = BytesIO(res.content)
        filename = f"{uuid.uuid4()}.jpg"
        return buffer, filename
    raise Exception("Failed to download thumbnail")

def upload_and_get_links(auth_token, file_buffer, filename, folder_id):
    file_buffer.seek(0)
    upload_res = requests.post("https://api.pcloud.com/uploadfile", params={
        "auth": auth_token,
        "folderid": folder_id
    }, files={"file": (filename, file_buffer)}).json()

    if upload_res.get("result") != 0:
        raise Exception(f"Upload failed: {upload_res}")

    fileid = upload_res["metadata"][0]["fileid"]

    # Get public page link
    publink_res = requests.get("https://api.pcloud.com/getfilepublink", params={
        "auth": auth_token,
        "fileid": fileid
    }).json()

    if publink_res.get("result") != 0:
        raise Exception(f"Public link failed: {publink_res}")

    public_page_link = publink_res.get("link")

    # Get direct stream/download link
    direct_res = requests.get("https://api.pcloud.com/getfilelink", params={
        "auth": auth_token,
        "fileid": fileid
    }).json()

    if direct_res.get("result") != 0:
        raise Exception(f"Direct link failed: {direct_res}")

    direct_link = direct_res["hosts"][0] + direct_res["path"]

    return public_page_link, direct_link

@app.get("/")
def home():
    return {"message": "YouTube to pCloud uploader is live!"}

@app.get("/upload")
def upload(link: str = Query(..., description="YouTube video URL")):
    try:
        if not AUTH_TOKEN:
            raise Exception("Missing PCLOUD_AUTH_TOKEN")

        songs_folder_id = get_or_create_folder(AUTH_TOKEN, SONGS_FOLDER)
        imgs_folder_id = get_or_create_folder(AUTH_TOKEN, IMGS_FOLDER)

        # Download MP3 and Thumbnail
        audio_buffer, audio_filename, thumb_url = download_audio_and_thumbnail(link)
        thumb_buffer, thumb_filename = download_thumbnail(thumb_url)

        # Upload both and get links
        mp3_public, mp3_direct = upload_and_get_links(AUTH_TOKEN, audio_buffer, audio_filename, songs_folder_id)
        jpg_public, jpg_direct = upload_and_get_links(AUTH_TOKEN, thumb_buffer, thumb_filename, imgs_folder_id)

        audio_buffer.close()
        thumb_buffer.close()

        return JSONResponse(content={
            "mp3": {
                "public_page": mp3_public,
                "direct_stream": mp3_direct
            },
            "thumbnail": {
                "public_page": jpg_public,
                "direct_link": jpg_direct
            }
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

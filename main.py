import os
import json
import requests
from datetime import datetime, timedelta, timezone

# =========================
# 1. 기본 설정
# =========================

CHANNELS = [
    "@디에스경제급등",
    "@디에스경제연구소DS",
    "@디에스황제주식TV",
    "@디에스경제타임즈",
    "@Power_bus2",
    "@DSnews77",
    "@문선생_경제교실",
]

# 🎯 채널별 관심 종목 필터
# 빈 리스트 [] = 해당 채널의 모든 새 영상 링크를 가져옵니다.
# 종목명이 있으면 = 제목/설명글에 해당 종목이 있을 때만 링크를 가져옵니다.
TARGET_STOCKS_BY_CHANNEL = {
    "@디에스경제급등": [],
    "@디에스경제연구소DS": [],
    "@디에스황제주식TV": [],
    "@디에스경제타임즈": [],
    "@DSnews77": [],
    "@Power_bus2": ["알테오젠", "196170", "클로봇", "466100", "삼성중공업", "010140"],
    "@문선생_경제교실": ["펩트론", "087010"], 
}

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "12"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "5"))
PROCESSED_FILE = "processed_videos.json"

# =========================
# 2. 필수 함수들
# =========================

def get_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value: return value
    raise RuntimeError(f"Secrets 누락: {', '.join(names)}")

def load_processed_ids():
    if not os.path.exists(PROCESSED_FILE): return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except: return set()

def save_processed_ids(processed_ids):
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)

def youtube_get(endpoint, params, api_key):
    params = dict(params)
    params["key"] = api_key
    url = f"{YOUTUBE_BASE_URL}/{endpoint}"
    response = requests.get(url, params=params, timeout=20).json()
    if "error" in response: raise RuntimeError(f"YouTube API 오류: {response.get('error')}")
    return response

# =========================
# 3. 필터(관심 종목) 확인 로직
# =========================

def normalize_text(text):
    return text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip().lower().replace(" ", "")

def get_target_keywords(channel_handle):
    return TARGET_STOCKS_BY_CHANNEL.get(channel_handle, [])

def check_keywords_match(channel_handle, title, description):
    keywords = get_target_keywords(channel_handle)
    
    if not keywords:
        return True, [] 

    combined_text = normalize_text(f"{title} {description}")
    matched = []
    
    for kw in keywords:
        if normalize_text(kw) in combined_text:
            matched.append(kw)

    if matched:
        return True, matched
    else:
        return False, []

# =========================
# 4. 채널 영상 수집
# =========================

def get_recent_videos(api_key, channel_handle):
    h = channel_handle.replace("@", "").strip()
    try:
        data = youtube_get("channels", {"part": "snippet,contentDetails", "forHandle": h}, api_key)
        if not data.get("items"): return []
        channel = data["items"][0]
        channel_title = channel["snippet"]["title"]
        uploads_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        
        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        videos = []
        
        v_data = youtube_get("playlistItems", {"part": "snippet,contentDetails", "playlistId": uploads_id, "maxResults": MAX_VIDEOS_PER_CHANNEL}, api_key)
        for item in v_data.get("items", []):
            pub_at = item["contentDetails"].get("videoPublishedAt", "")
            if not pub_at: continue
            
            pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
            if pub_dt < cutoff: continue
            
            videos.append({
                "channel_handle": channel_handle,
                "channel": channel_title,
                "title": item["snippet"]["title"],
                "description": item["snippet"]["description"],
                "url": f"https://www.youtube.com/watch?v={item['contentDetails']['videoId']}",
                "video_id": item["contentDetails"]["videoId"]
            })
        return videos
    except Exception as e:
        print(f"⚠️ 채널 조회 실패: {channel_handle} / {e}")
        return []

def send_telegram(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}, timeout=20)
        return True
    except: return False

# =========================
# 5. 메인 실행 (프롬프트 결합)
# =========================

def main():
    print("🚀 주식 유튜브 새 영상 수집 시작 (종목 필터 적용 모드)")
    
    youtube_api_key = get_env("YOUTUBE_API_KEY")
    telegram_token = get_env("TELEGRAM_TOKEN")
    telegram_chat_id = get_env("TELEGRAM_CHAT_ID")

    processed_ids = load_processed_ids()
    new_videos = []

    for channel_handle in CHANNELS:
        videos = get_recent_videos(youtube_api_key, channel_handle)
        for v in videos:
            if v["video_id"] in processed_ids:
                continue
                
            is_matched, matched_kws = check_keywords_match(v["channel_handle"], v["title"], v["description"])
            
            if is_matched:
                v["matched"] = matched_kws 
                new_videos.append(v)
            else:
                processed_ids.add(v["video_id"])

    if not new_videos:
        print("⏭️ 새로 올라온 영상 중 관심 있는 영상이 없습니다.")
        save_processed_ids(processed_ids)
        return

    print(f"📦 필터 통과 신규 영상 {len(new_videos)}개 발견! 프롬프트 생성 중...")

    # ==========================================
    # 💡 텔레그램으로 보낼 "제미나이 복붙용 프롬프트" 조립
    # ==========================================
    message = """아래 유튜브 영상 링크들을 처음부터 끝까지 꼼꼼하게 시청하고 분석해서, 바쁜 주식 투자자를 위해 핵심만 요약해줘.

[매우 중요한 작성 원칙 - 절대 지킬 것]
1. 누락 금지: 영상에서 유튜버가 추천/분석하는 메인 종목이 여러 개(2~3개 등)라면, 절대 하나만 적고 끝내지 말고 빠짐없이 모두 다 요약해라.
2. 가격 정보 조작 금지: 매수가, 목표가, 손절가 등은 **반드시 유튜버가 영상에서 입으로 직접 말한 가격**만 적어라. 네가 임의로 현재가를 검색해서 섞거나 지어내면 절대 안 된다. (언급이 없으면 "언급 없음"이라고 적거나 비워둬라)
3. 종목코드는 적지 말고 종목명만 사용해.
4. 각 영상 링크 옆에 [🎯관심종목]이 지정된 경우, 무조건 해당 종목 위주로 파고들어.
5. 스쳐 지나가듯 단순 언급된 종목은 무조건 요약에서 빼.
6. 반드시 영상을 처음부터 끝까지 살펴봐야해. 
7. ds경제뉴스채널의 영상은 특히더 신경써서 봐야해. 앞부분에 하는 오늘 시황정리와 뒷부분에 하는 추천 종목 2~3개는 꼭 잘 정리해줘

[출력 양식 (영상 1개당, 추천 종목 개수만큼 아래 종목 폼 반복)]
📺 영상 제목 (채널명)
- 핵심 요약: (2~3줄)

⭐ [추천/관심 종목명 1]
- 가격 전략: 진입가 / 목표가 / 세력목표가 / 손절가 (영상에서 말한 것만 정확히)
- 상승 논리: (불릿 1~2개)

⭐ [추천/관심 종목명 2] (※ 영상에 종목이 여러 개일 경우 폼 계속 추가)
...

[영상 링크 목록]
"""
    
    for i, v in enumerate(new_videos, 1):
        target_tag = f" [🎯관심종목: {', '.join(v['matched'])} 집중 요약]" if v['matched'] else ""
        message += f"{i}. {v['url']} (채널: {v['channel']} / 제목: {v['title']}){target_tag}\n"
        processed_ids.add(v["video_id"])

    if send_telegram(telegram_token, telegram_chat_id, message.strip()):
        print("🚀 텔레그램 전송 완료!")
        save_processed_ids(processed_ids)
    else:
        print("❌ 텔레그램 전송 실패")

if __name__ == "__main__":
    main()

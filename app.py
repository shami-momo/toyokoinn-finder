import json, math, os, smtplib, ssl, threading, time, uuid
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
CHECK_INTERVAL_MINUTES = 30
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA = DATA_DIR / "data.json"
LEGACY_DATA = ROOT / "data.json"
lock = threading.RLock()
scan_lock = threading.Lock()
geocode_lock = threading.Lock()
last_geocode = 0.0
session = requests.Session()
session.headers.update({"User-Agent": "ToyokoMonitor/1.0 (personal vacancy monitoring)", "Accept-Language": "ja"})
state = {"config": {}, "results": {}, "rate": None, "catalog": [], "coordinates": {}, "status": "대기 중", "last_check": None, "next_check": None}
if not DATA.exists() and LEGACY_DATA.exists():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.replace(LEGACY_DATA, DATA)
    except OSError:
        pass
saved_data = DATA if DATA.exists() else LEGACY_DATA
legacy_hotel_data = False
if saved_data.exists():
    try:
        saved = json.loads(saved_data.read_text(encoding="utf-8"))
        legacy_hotel_data = "catalog" in saved or "coordinates" in saved
        state.update(saved)
    except (ValueError, OSError):
        pass
if not state.get("catalog") or not state.get("coordinates"):
    try:
        hotel_file = DATA_DIR / "hotel_data.json"
        if not hotel_file.exists():
            hotel_file = ROOT / "hotel_data.json"
        hotel_data = json.loads(hotel_file.read_text(encoding="utf-8"))
        if not state.get("catalog") and isinstance(hotel_data.get("catalog"), list):
            state["catalog"] = hotel_data["catalog"]
        if not state.get("coordinates") and isinstance(hotel_data.get("coordinates"), dict):
            state["coordinates"] = hotel_data["coordinates"]
    except (OSError, ValueError, AttributeError):
        pass
if "groups" not in state:
    old = state.get("config", {})
    keys = ("start", "end", "people", "room_type", "smoking", "student", "hotels")
    if old.get("hotels") and all(key in old for key in keys):
        state["groups"] = [{**{key: old[key] for key in keys}, "id": uuid.uuid4().hex, "results": state.get("results", {})}]
    else:
        state["groups"] = []
state["status"] = "대기 중"
state["next_check"] = None

def save():
    with lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DATA.with_suffix(".tmp")
        personal = {key: value for key, value in state.items() if key not in ("catalog", "coordinates")}
        tmp.write_text(json.dumps(personal, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA)

def save_hotel_data():
    with lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        hotel_file = DATA_DIR / "hotel_data.json"
        tmp = hotel_file.with_suffix(".tmp")
        hotel_data = {"catalog": state["catalog"], "coordinates": state["coordinates"]}
        tmp.write_text(json.dumps(hotel_data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, hotel_file)

if legacy_hotel_data:
    save_hotel_data()
    save()

def get(url, **kwargs):
    response = session.get(url, timeout=25, **kwargs)
    response.raise_for_status()
    return response

def next_data(html):
    tag = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if not tag:
        raise ValueError("공식 사이트의 검색 데이터를 읽을 수 없습니다.")
    return json.loads(tag.get_text())["props"]["pageProps"]

def korean_names(hotels):
    # Names are only labels; all availability and price requests use the Japanese site.
    try:
        page = next_data(get("https://www.toyoko-inn.com/korea/hotel_list/").text)
        names = {}
        def walk(obj):
            if isinstance(obj, dict):
                if obj.get("hotelCode") and obj.get("name"):
                    names[str(obj["hotelCode"]).zfill(5)] = obj["name"]
                for value in obj.values(): walk(value)
            elif isinstance(obj, list):
                for value in obj: walk(value)
        walk(page)
        if not names: return hotels
        for h in hotels:
            h["name_ja"] = h["name"]
            h["name"] = names.get(h["code"], h["name"])
        with lock:
            state["catalog"] = hotels
            save_hotel_data()
    except Exception:
        pass
    return hotels

def catalog():
    if state["catalog"] and all("name_ja" in h for h in state["catalog"]):
        return state["catalog"]
    if state["catalog"]:
        hotels = state["catalog"]
        return korean_names(hotels)
    page = next_data(get("https://www.toyoko-inn.com/hotel_list/").text)
    rows = {}
    def walk(obj):
        if isinstance(obj, dict):
            code = obj.get("hotelCode")
            if code and str(code).isdigit() and (not rows.get(str(code).zfill(5)) or len(obj) > len(rows[str(code).zfill(5)])):
                rows[str(code).zfill(5)] = obj
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj: walk(v)
    walk(page)
    hotels = [{"code": code, "name": row.get("name") or code} for code, row in rows.items() if row.get("name") and str(row.get("country", "")).lower() in ("", "jpn", "japan", "jp", "1")]
    if not hotels:
        raise ValueError("공식 호텔 목록을 읽을 수 없습니다.")
    hotels.sort(key=lambda x: x["name"])
    with lock:
        state["catalog"] = hotels
        save_hotel_data()
    return korean_names(hotels)

def exchange():
    now = time.time()
    cached = state.get("rate")
    if cached and now - cached.get("checked", 0) < 86400:
        return cached
    try:
        data = get("https://api.frankfurter.dev/v2/rate/jpy/krw").json()
        rate = {"value": float(data["rate"]), "date": data["date"], "checked": now}
        with lock:
            state["rate"] = rate
            save()
        return rate
    except Exception:
        if cached: return cached
        return None

def geocode(query):
    global last_geocode
    queries = [query]
    if query.endswith("역") and query[:-1].strip():
        queries.append(query[:-1].strip())
    for index, name in enumerate(queries):
        with geocode_lock:
            time.sleep(max(0, 1.1 - (time.monotonic() - last_geocode)))
            last_geocode = time.monotonic()
            geo = get("https://nominatim.openstreetmap.org/search", params={"q": name, "format": "jsonv2", "limit": 10 if index else 1, "countrycodes": "jp", "accept-language": "ko,ja,en"}, headers={"User-Agent": "ToyokoKoreanMonitor/1.0"}).json()
        if index:
            geo = [item for item in geo if item.get("category", item.get("class")) == "railway" and item.get("type") in ("station", "halt", "stop")]
            geo.sort(key=lambda item: item.get("type") != "station")
        if geo:
            return geo[0]
    raise ValueError("장소를 찾지 못했습니다. 다른 한국어 표기 또는 일본어·영어 이름을 입력해 주세요.")

def find_nearby(query, radius):
    if not query or not 0 < radius <= 100:
        raise ValueError("장소와 0~100km 이내의 반경을 입력해 주세요.")
    geo = geocode(query)
    lat, lon = float(geo["lat"]), float(geo["lon"])
    coords = dict(state.get("coordinates", {}))
    if not coords:
        today = date.today()
        for pref in range(1, 48):
            params = {"prefecture": pref, "start": today.isoformat(), "end": (today + timedelta(days=1)).isoformat(), "people": 1, "room": 1, "smoking": "all"}
            page = next_data(get("https://www.toyoko-inn.com/search/result/", params=params).text)
            rows = page.get("searchResponse", {}).get("hotels")
            if not isinstance(rows, list):
                raise ValueError("일본 공식 호텔 위치 정보를 읽지 못했습니다.")
            for h in rows:
                if h.get("hotelCode") and h.get("latDegree") is not None and h.get("lngDegree") is not None:
                    coords[str(h["hotelCode"]).zfill(5)] = [float(h["latDegree"]), float(h["lngDegree"])]
            time.sleep(0.5)
        if not coords:
            raise ValueError("호텔 위치 정보를 찾지 못했습니다.")
        with lock:
            state["coordinates"] = coords
            save_hotel_data()
    result = []
    for h in catalog():
        c = coords.get(h["code"])
        if not c: continue
        a = math.sin(math.radians(c[0] - lat)/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(c[0]))*math.sin(math.radians(c[1] - lon)/2)**2
        distance = 12742 * math.asin(min(1, math.sqrt(a)))
        if distance <= radius:
            result.append({**h, "distance": round(distance, 1), "lat": c[0], "lon": c[1]})
    return sorted(result, key=lambda x: x["distance"]), geo.get("display_name", ""), {"lat": lat, "lon": lon}

def validate(data):
    try:
        start = date.fromisoformat(data.get("start", ""))
        end = date.fromisoformat(data.get("end", ""))
    except (TypeError, ValueError):
        raise ValueError("체크인과 체크아웃 날짜를 선택해 주세요.") from None
    if start < date.today() or end <= start or (end-start).days > 7:
        raise ValueError("체크인과 체크아웃 날짜를 확인해 주세요. 최대 7박까지 검색합니다.")
    people = int(data["people"])
    if not 1 <= people <= 5: raise ValueError("인원은 1~5명으로 선택해 주세요.")
    if data.get("room_type") not in ("all", "single", "double", "twin"): raise ValueError("객실 유형이 올바르지 않습니다.")
    if data.get("smoking") not in ("all", "noSmoking", "Smoking"): raise ValueError("흡연 조건이 올바르지 않습니다.")
    hotels = data.get("hotels")
    if not isinstance(hotels, list) or not hotels or len(hotels) > 100: raise ValueError("호텔을 1~100개 선택해 주세요.")
    available = {h["code"]: h for h in catalog()}
    codes = list(dict.fromkeys(str(h) for h in hotels))
    if any(code not in available for code in codes): raise ValueError("호텔 목록을 다시 불러와 선택해 주세요.")
    previous = state.get("config", {})
    use_telegram = data.get("telegram_enabled") is True
    use_email = data.get("email_enabled") is True
    token = str(data.get("token") or "").strip() or previous.get("token", "")
    chat = str(data.get("chat") or "").strip()
    if use_telegram and (not token or not chat):
        raise ValueError("텔레그램 봇 토큰과 채팅 ID를 입력해 주세요.")
    smtp_host = str(data.get("smtp_host") or "").strip()
    smtp_user = str(data.get("smtp_user") or "").strip()
    smtp_password = str(data.get("smtp_password") or "") or previous.get("smtp_password", "")
    email_to = str(data.get("email_to") or "").strip()
    smtp_security = data.get("smtp_security", "starttls")
    try:
        smtp_port = int(data.get("smtp_port") or 587)
    except (TypeError, ValueError):
        raise ValueError("SMTP 포트를 확인해 주세요.")
    if use_email:
        if not all((smtp_host, smtp_user, smtp_password, email_to)):
            raise ValueError("이메일의 SMTP 서버, 계정, 비밀번호와 받는 주소를 입력해 주세요.")
        if any("\n" in v or "\r" in v for v in (smtp_host, smtp_user, email_to)):
            raise ValueError("이메일 설정을 확인해 주세요.")
        if not 1 <= smtp_port <= 65535 or smtp_security not in ("starttls", "ssl"):
            raise ValueError("SMTP 포트와 보안 방식을 확인해 주세요.")
        if parseaddr(smtp_user)[1] != smtp_user or "@" not in smtp_user or parseaddr(email_to)[1] != email_to or "@" not in email_to:
            raise ValueError("SMTP 계정과 받는 이메일 주소를 확인해 주세요.")
    return {"start": start.isoformat(), "end": end.isoformat(), "people": people, "room_type": data["room_type"], "smoking": data["smoking"], "student": bool(data.get("student")), "hotels": codes, "telegram_enabled": use_telegram, "token": token, "chat": chat, "email_enabled": use_email, "smtp_host": smtp_host, "smtp_port": smtp_port, "smtp_security": smtp_security, "smtp_user": smtp_user, "smtp_password": smtp_password, "email_to": email_to, "enabled": True}

def search_hotel(code, cfg):
    params = {"hotel": code, "people": cfg["people"], "room": 1, "smoking": cfg["smoking"], "start": cfg["start"], "end": cfg["end"]}
    url = "https://www.toyoko-inn.com/search/result/room_plan/?" + urlencode(params)
    page = next_data(get(url).text)
    plan = page.get("planResponse")
    if not isinstance(plan, dict) or not isinstance(plan.get("roomTypeList"), list):
        raise ValueError("객실 검색 결과 형식이 변경되었거나 검색할 수 없습니다.")
    offers = []
    names = {"single": "シングル", "double": "ダブル", "twin": "ツイン"}
    for room in plan["roomTypeList"]:
        name = room.get("roomTypeName", "")
        if cfg["room_type"] != "all" and names[cfg["room_type"]] not in name: continue
        specs = room.get("specs") or {}
        smoke = specs.get("isSmoking")
        if cfg["smoking"] != "all" and (smoke is None or bool(smoke) != (cfg["smoking"] == "Smoking")): continue
        for p in room.get("plans") or []:
            pname = p.get("planName", "")
            student = "学割" in pname
            if student and not cfg["student"]: continue
            vacancy = p.get("vacant") or {}
            general = int(vacancy.get("generalVacantRoom") or 0)
            member = int(vacancy.get("membershipVacantRoom") or 0)
            if max(general, member) < 1: continue
            price = p.get("price") or {}
            room_label = name
            for ja, ko in (("\u30b7\u30f3\u30b0\u30eb", "\uc2f1\uae00"), ("\u30c0\u30d6\u30eb", "\ub354\ube14"), ("\u30c4\u30a4\u30f3", "\ud2b8\uc708"), ("\u30a8\u30b3\u30ce\u30df\u30fc", "\uc774\ucf54\ub178\ubbf8"), ("\u30c7\u30e9\u30c3\u30af\u30b9", "\ub514\ub7ed\uc2a4")):
                room_label = room_label.replace(ja, ko)
            plan_names = {"スタンダードプラン": "표준 플랜", "学割プラン": "학생 할인", "【休日限定】お得プラン！": "휴일 한정 특가", "【曜日限定】お得プラン！": "요일 한정 특가", "歳末・新春セール": "연말연시 세일"}
            offers.append({"room": room_label, "smoking": "흡연" if smoke else "금연" if smoke is False else "미확인", "plan": plan_names.get(pname, pname or "플랜명 미제공"), "general": price.get("generalPrice"), "member": price.get("membershipPrice"), "general_available": general > 0, "member_available": member > 0})
    category = "member" if any(offer["member_available"] for offer in offers) else "general"
    candidates = [offer for offer in offers if offer[f"{category}_available"]]
    selected = [min(candidates, key=lambda offer: (offer[category] is None, offer[category] if offer[category] is not None else 0))] if candidates else []
    other_plans_count = len(offers) - len(selected)
    label = next((h["name"] for h in state["catalog"] if h["code"] == code), None)
    return {"name": label or plan.get("hotelTitle") or code, "url": url, "offers": selected, "other_plans_count": other_plans_count, "status": "빈방 있음" if offers else "빈방 없음", "checked": datetime.now().isoformat(timespec="seconds")}

class TelegramError(Exception):
    pass

def telegram(cfg, text):
    try:
        response = session.post("https://api.telegram.org/bot" + cfg["token"].strip() + "/sendMessage", json={"chat_id": cfg["chat"].strip(), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
    except requests.RequestException:
        raise TelegramError("연결하지 못했습니다. 인터넷 연결을 확인해 주세요.") from None
    try:
        payload = response.json()
    except ValueError:
        raise TelegramError("서버 응답을 확인하지 못했습니다.") from None
    if response.ok and payload.get("ok"):
        return
    description = str(payload.get("description", "")).lower()
    if response.status_code == 401:
        reason = "봇 토큰이 올바르지 않습니다. BotFather에서 토큰을 확인해 주세요."
    elif "chat not found" in description:
        reason = "채팅 ID를 확인하고 봇과의 대화에서 시작을 눌러 주세요."
    elif "blocked" in description:
        reason = "봇이 차단되어 있습니다. 차단을 해제하고 다시 시작해 주세요."
    elif response.status_code == 403:
        reason = "봇이 메시지를 보낼 수 없습니다. 대화 시작 여부와 권한을 확인해 주세요."
    elif response.status_code == 429:
        reason = "요청이 너무 많습니다. 잠시 후 다시 시도합니다."
    else:
        reason = f"전송하지 못했습니다. (HTTP {response.status_code})"
    raise TelegramError(reason)

def telegram_message(result, conditions, rate):
    name = escape(result["name"])
    url = escape(result["url"], quote=True)
    lines = [f'<b><a href="{url}">{name}</a></b>', "", f"{escape(conditions['start'])} ~ {escape(conditions['end'])} · {conditions['people']}명"]

    def price(value, available):
        if value is None:
            text = "미제공"
        else:
            text = f"¥{value:,}" + (f" · ₩{round(value * rate['value']):,}" if rate else "")
        return text + (" (예약 불가)" if not available else "")

    for offer in result["offers"]:
        description = " · ".join(escape(str(offer[key])) for key in ("room", "smoking", "plan"))
        lines.extend(["", f"<b>{description}</b>", f"일반가 {price(offer['general'], offer['general_available'])}", f"회원가 {price(offer['member'], offer['member_available'])}"])
    if "other_plans_count" in result:
        lines.extend(["", f"기타 플랜 {result['other_plans_count']}건"])
    return "\n".join(lines)

def email_alert(cfg, text):
    message = EmailMessage()
    message["Subject"] = "토요코인 빈방 알림"
    message["From"] = cfg["smtp_user"]
    message["To"] = cfg["email_to"]
    message.set_content(text)
    context = ssl.create_default_context()
    if cfg["smtp_security"] == "ssl":
        client = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=20, context=context)
    else:
        client = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20)
    with client:
        if cfg["smtp_security"] == "starttls":
            client.starttls(context=context)
        client.login(cfg["smtp_user"], cfg["smtp_password"])
        client.send_message(message)

GROUP_FIELDS = ("start", "end", "people", "room_type", "smoking", "student", "hotels")

def run_groups():
    if not scan_lock.acquire(blocking=False):
        return
    try:
        with lock:
            settings = dict(state["config"])
            if not settings.get("enabled"):
                return
            group_ids = [group["id"] for group in state["groups"]]
            state["status"] = "검색 중"
        exchange()
        changed = False
        first = True
        for group_id in group_ids:
            with lock:
                group = next((item for item in state["groups"] if item["id"] == group_id), None)
                if not group or not state["config"].get("enabled"):
                    changed = True
                    continue
                conditions = {key: group[key] for key in GROUP_FIELDS}
                conditions["hotels"] = list(conditions["hotels"])
            if conditions["end"] <= date.today().isoformat():
                continue
            for code in conditions["hotels"]:
                if not first:
                    time.sleep(2)
                first = False
                try:
                    result = search_hotel(code, conditions)
                    with lock:
                        current = next((item for item in state["groups"] if item["id"] == group_id), None)
                        if not current or not state["config"].get("enabled") or any(current.get(k) != conditions[k] for k in GROUP_FIELDS) or state["config"] != settings:
                            changed = True
                            break
                        old = current.setdefault("results", {}).get(code, {})
                        if result["offers"]:
                            result["availability_id"] = (old.get("availability_id") if old.get("offers") else None) or uuid.uuid4().hex
                        notified = dict(old.get("notified_channels") or {})
                        if old.get("notified") and not notified:
                            notified["telegram"] = True
                        channels = []
                        if settings.get("telegram_enabled", bool(settings.get("token"))): channels.append(("telegram", "텔레그램", telegram))
                        if settings.get("email_enabled"): channels.append(("email", "이메일", email_alert))
                        if result["offers"]:
                            lines = [f"빈방 발견: {result['name']}", f"{conditions['start']} ~ {conditions['end']} / {conditions['people']}명"]
                            rate = state.get("rate")
                            def fmt(value):
                                if value is None: return "미제공"
                                return f"¥{value:,}" + (f" (약 ₩{round(value * rate['value']):,})" if rate else "")
                            for offer in result["offers"]:
                                general_status = "" if offer["general_available"] else " (예약 불가)"
                                member_status = "" if offer["member_available"] else " (예약 불가)"
                                lines.append(f"{offer['room']} / {offer['smoking']} / {offer['plan']}: 일반 {fmt(offer['general'])}{general_status}, 회원 {fmt(offer['member'])}{member_status}")
                            if "other_plans_count" in result:
                                lines.append(f"기타 플랜 {result['other_plans_count']}건")
                            lines.append(result["url"])
                            failed = []
                            for key, label, send in channels:
                                if not notified.get(key):
                                    try:
                                        message = telegram_message(result, conditions, rate) if key == "telegram" else "\n".join(lines)
                                        send(settings, message)
                                        notified[key] = True
                                    except TelegramError as exc:
                                        failed.append(f"{label}: {exc}")
                                    except Exception:
                                        failed.append(f"{label} 전송 실패.")
                            if failed:
                                result["error"] = f"{' '.join(failed)} 다음 확인 때 다시 시도합니다."
                        result["notified_channels"] = notified if result["offers"] else {}
                        current["results"][code] = result
                        save()
                except Exception:
                    with lock:
                        current = next((item for item in state["groups"] if item["id"] == group_id), None)
                        if current and state["config"].get("enabled") and all(current.get(k) == conditions[k] for k in GROUP_FIELDS) and state["config"] == settings:
                            old = dict(current.setdefault("results", {}).get(code, {}))
                            old.update({"status": "확인 오류", "error": "공식 사이트에 접속하거나 결과를 읽지 못했습니다.", "checked": datetime.now().isoformat(timespec="seconds")})
                            current["results"][code] = old
                            save()
                        else:
                            changed = True
        with lock:
            if state["config"].get("enabled"):
                changed = changed or group_ids != [group["id"] for group in state["groups"]] or state["config"] != settings
                state["last_check"] = datetime.now().isoformat(timespec="seconds")
                state["status"] = "감시 중"
                state["next_check"] = None if changed else (datetime.now() + timedelta(minutes=CHECK_INTERVAL_MINUTES)).isoformat(timespec="seconds")
                save()
    finally:
        scan_lock.release()

def worker():
    while True:
        if state["config"].get("enabled"):
            if not any(group.get("end", "") > date.today().isoformat() for group in state["groups"]):
                with lock:
                    state["config"]["enabled"] = False
                    state["status"] = "\uac10\uc2dc \uae30\uac04 \uc885\ub8cc"
                    state["next_check"] = None
                    save()
                continue
            nxt = state.get("next_check")
            if not nxt or datetime.fromisoformat(nxt) <= datetime.now():
                run_groups()
        time.sleep(5)

@app.after_request
def prevent_page_cache(response):
    if request.endpoint in ("index", "static"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/state")
def api_state():
    with lock:
        cfg = dict(state["config"])
        cfg["token_set"] = bool(cfg.pop("token", ""))
        cfg["smtp_password_set"] = bool(cfg.pop("smtp_password", ""))
        cfg.setdefault("telegram_enabled", cfg["token_set"])
        cfg.setdefault("email_enabled", False)
        return jsonify(config=cfg, groups=state["groups"], rate=state["rate"], status=state["status"], last_check=state["last_check"], next_check=state["next_check"], interval_minutes=CHECK_INTERVAL_MINUTES)

@app.get("/api/hotels")
def api_hotels():
    try: return jsonify(catalog())
    except Exception: return jsonify(error="호텔 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."), 502

@app.get("/api/rate")
def api_rate():
    return jsonify(exchange())

@app.post("/api/nearby")
def api_nearby():
    try:
        d = request.get_json()
        hotels, place, center = find_nearby(str(d.get("place", "")).strip(), float(d.get("radius", 0)))
        return jsonify(hotels=hotels, place=place, center=center)
    except ValueError as e: return jsonify(error=str(e)), 400
    except Exception: return jsonify(error="장소 또는 호텔 위치를 불러오지 못했습니다."), 502

@app.post("/api/start")
def api_start():
    try:
        with lock:
            group = next((item for item in state["groups"] if item["end"] > date.today().isoformat()), None)
        if not group:
            raise ValueError("감시할 조건을 먼저 추가해 주세요.")
        cfg = validate({**request.get_json(), **{key: group[key] for key in GROUP_FIELDS}})
        with lock:
            state["config"] = cfg
            state["next_check"] = None
            state["status"] = "감시 시작 대기 중"
            save()
        return jsonify(ok=True)
    except (ValueError, KeyError, TypeError) as e: return jsonify(error=str(e)), 400
    except Exception: return jsonify(error="설정을 저장하지 못했습니다."), 500

@app.post("/api/groups")
def api_add_group():
    try:
        data = request.get_json()
        validated = validate({**data, "telegram_enabled": False, "email_enabled": False})
        group = {key: validated[key] for key in GROUP_FIELDS}
        group.update(id=uuid.uuid4().hex, results={})
        with lock:
            state["groups"].append(group)
            state["next_check"] = None
            save()
        return jsonify(ok=True, group=group)
    except (ValueError, KeyError, TypeError) as e: return jsonify(error=str(e)), 400
    except Exception: return jsonify(error="감시 조건을 저장하지 못했습니다."), 500

@app.delete("/api/groups/<group_id>")
def api_remove_group(group_id):
    with lock:
        state["groups"] = [group for group in state["groups"] if group["id"] != group_id]
        if not state["groups"]:
            state["config"]["enabled"] = False
            state["status"] = "중지됨"
        state["next_check"] = None
        save()
    return jsonify(ok=True)

@app.delete("/api/groups/<group_id>/hotels/<code>")
def api_remove_group_hotel(group_id, code):
    with lock:
        group = next((g for g in state["groups"] if g["id"] == group_id), None)
        if group:
            group["hotels"] = [hotel for hotel in group["hotels"] if hotel != code]
            group["results"].pop(code, None)
            if not group["hotels"]:
                state["groups"].remove(group)
            if not state["groups"]:
                state["config"]["enabled"] = False
                state["status"] = "중지됨"
            state["next_check"] = None
            save()
    return jsonify(ok=True)

@app.post("/api/stop")
def api_stop():
    with lock:
        state["config"]["enabled"] = False
        state["status"] = "중지됨"
        state["next_check"] = None
        save()
    return jsonify(ok=True)

if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    app.run(host="127.0.0.1", port=4180, debug=False, use_reloader=False)


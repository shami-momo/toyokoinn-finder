const $ = (id) => document.getElementById(id);
let hotels = [],
  shown = [],
  selected = new Set(),
  rate = null,
  initialized = false;
let map = null,
  mapLayers = null,
  hotelMarkers = new Map();
let groups = [];
function escapeHtml(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
async function api(url, options) {
  let r = await fetch(url, options),
    v = await r.json();
  if (!r.ok) throw Error(v.error || "요청에 실패했습니다.");
  return v;
}
function showChannels() {
  $("telegram_fields").hidden = !$("telegram_enabled").checked;
  $("email_fields").hidden = !$("email_enabled").checked;
  $("browser_hint").hidden =
    $("telegram_enabled").checked || $("email_enabled").checked;
}
$("telegram_enabled").onchange = showChannels;
$("email_enabled").onchange = showChannels;

function setupHelpDialog() {
  const dialog = $("helpDialog");
  document.querySelectorAll("[data-help]").forEach((button) => {
    button.addEventListener("click", () => {
      const isTelegram = button.dataset.help === "telegram";
      $("helpTitle").textContent = isTelegram ? "텔레그램 설정" : "이메일 설정";
      $("telegramHelp").hidden = !isTelegram;
      $("emailHelp").hidden = isTelegram;
      dialog.showModal();
    });
  });
  $("closeHelp").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
}
setupHelpDialog();

function displayHotels() {
  let q = $("filter").value.trim().toLowerCase(),
    list = shown.filter((h) =>
      (h.name + " " + (h.name_ja || "") + " " + h.code)
        .toLowerCase()
        .includes(q),
    );
  $("count").textContent =
    "선택 " + selected.size + "개 · 표시 " + list.length + "개";
  $("hotels").innerHTML = list.length
    ? list
        .map(
          (h) =>
            '<label class="hotel"><input type="checkbox" data-code="' +
            escapeHtml(h.code) +
            '" ' +
            (selected.has(h.code) ? "checked" : "") +
            "> " +
            escapeHtml(h.name) +
            " (" +
            escapeHtml(h.code) +
            ")" +
            (h.distance != null ? " · " + h.distance + "km" : "") +
            "</label>",
        )
        .join("")
    : "표시할 호텔이 없습니다.";
  hotelMarkers.forEach((marker, code) =>
    marker.setStyle({
      fillColor: selected.has(code) ? "#2859c5" : "#fff",
      fillOpacity: 1,
    }),
  );
  renderSelected();
}
$("hotels").addEventListener("change", (e) => {
  if (e.target.matches("[data-code]")) {
    if (e.target.checked) {
      selected.add(e.target.dataset.code);
      displayHotels();
    } else {
      removeHotel(e.target.dataset.code);
    }
  }
});
$("filter").oninput = displayHotels;
$("all").onclick = () => {
  shown = hotels;
  $("map").hidden = true;
  $("mapSource").hidden = true;
  hotelMarkers.clear();
  $("placeNote").textContent = "전체 호텔 목록";
  displayHotels();
};
function showMap(data, radius) {
  if (typeof L === "undefined") {
    $("map").hidden = true;
    $("mapSource").hidden = true;
    $("placeNote").textContent += " · 지도를 불러오지 못했습니다. 호텔 목록을 이용해 주세요.";
    return;
  }
  $("map").hidden = false;
  $("mapSource").hidden = false;
  if (!map) {
    map = L.map("map", { attributionControl: false });
    L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
      minZoom: 5,
      maxZoom: 19,
      maxNativeZoom: 18,
    }).addTo(map);
    map.on("zoomend", () => {
      $("mapShoreline").hidden = map.getZoom() > 8;
    });
    mapLayers = L.layerGroup().addTo(map);
  }
  mapLayers.clearLayers();
  hotelMarkers.clear();
  const center = [data.center.lat, data.center.lon];
  map.setView(center, 12);
  const circle = L.circle(center, {
    radius: radius * 1000,
    color: "#2859c5",
    weight: 2,
    fillOpacity: 0.08,
  }).addTo(mapLayers);
  L.circleMarker(center, {
    radius: 5,
    color: "#222",
    fillColor: "#222",
    fillOpacity: 1,
  })
    .bindTooltip("검색 중심")
    .addTo(mapLayers);
  for (const hotel of data.hotels) {
    const marker = L.circleMarker([hotel.lat, hotel.lon], {
      radius: 6,
      color: "#2859c5",
      weight: 2,
      fillColor: selected.has(hotel.code) ? "#2859c5" : "#fff",
      fillOpacity: 1,
    }).addTo(mapLayers);
    const label = document.createElement("span");
    label.textContent = hotel.name + " · " + hotel.distance + "km";
    marker.bindTooltip(label);
    marker.on("click", () => {
      if (selected.has(hotel.code)) {
        removeHotel(hotel.code);
      } else {
        selected.add(hotel.code);
        displayHotels();
      }
    });
    hotelMarkers.set(hotel.code, marker);
  }
  map.invalidateSize();
  map.fitBounds(circle.getBounds(), { padding: [16, 16], maxZoom: 15 });
}
$("nearby").onclick = async () => {
  let b = $("nearby");
  b.disabled = true;
  $("error").textContent = "";
  $("placeNote").textContent =
    "검색중. 시간이 걸릴 수 있습니다.";
  try {
    let radius = Number($("radius").value),
      d = await api("/api/nearby", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place: $("place").value, radius }),
      });
    shown = d.hotels;
    $("placeNote").textContent = d.place + " 기준 직선거리";
    try {
      showMap(d, radius);
    } catch (e) {
      $("map").hidden = true;
      $("mapSource").hidden = true;
      $("placeNote").textContent += " · 지도를 표시하지 못했습니다. 호텔 목록을 이용해 주세요.";
      hotelMarkers.clear();
    }
    displayHotels();
  } catch (e) {
    $("map").hidden = true;
    $("mapSource").hidden = true;
    hotelMarkers.clear();
    $("error").textContent = e.message;
    $("placeNote").textContent = "주변 호텔 검색에 실패했습니다.";
  } finally {
    b.disabled = false;
  }
};
function money(n) {
  if (n == null) return "정보 없음";
  return (
    "¥" +
    Number(n).toLocaleString() +
    (rate ? " (₩" + Math.round(n * rate.value).toLocaleString() + ")" : "")
  );
}

function renderSelected() {
  const entries = [...selected];
  $("draftHotels").innerHTML = entries.length ? entries.map((code) => {
    const hotel = hotels.find((item) => item.code === code);
    const name = escapeHtml(hotel?.name || code);
    return `<div class="draft-hotel">
      <div class="selected-heading"><strong>${name}</strong>
        <button type="button" class="remove-hotel" data-remove="${escapeHtml(code)}" aria-label="${name} 선택 취소">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      </div>
    </div>`;
  }).join("") : '<p class="muted">선택한 호텔이 없어요</p>';
}

function removeHotel(code) {
  selected.delete(code);
  displayHotels();
}

$("draftHotels").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (button) removeHotel(button.dataset.remove);
});

function renderGroups() {
  const roomLabels = {all: "전체", single: "싱글", double: "더블", twin: "트윈"};
  const smokingLabels = {all: "전체", noSmoking: "금연", Smoking: "흡연"};
  $("results").innerHTML = groups.length ? groups.map((group) => {
    const groupId = escapeHtml(group.id);
    const list = group.hotels.map((code) => {
      const result = group.results?.[code];
      const hotel = hotels.find((item) => item.code === code);
      const name = escapeHtml(hotel?.name || result?.name || code);
      const url = escapeHtml(result?.url || `https://www.toyoko-inn.com/search/detail/${encodeURIComponent(code)}/`);
      const status = result?.status || "확인 대기";
      const statusClass = {"빈방 있음": "available", "빈방 없음": "unavailable", "확인 오류": "error"}[status] || "waiting";
      const checked = result?.checked ? ` · ${escapeHtml(result.checked.replace("T", " ").slice(0, 16))} 확인` : "";
      const offers = result?.offers?.length ? `
        <table><thead><tr><th>객실 / 플랜</th><th>비회원가</th><th>회원가</th></tr></thead><tbody>
        ${result.offers.map((offer) => `<tr><td>${escapeHtml(offer.room)} · ${escapeHtml(offer.smoking)} · ${escapeHtml(offer.plan)}</td>
          <td>${escapeHtml(money(offer.general))}${offer.general_available ? "" : "<br>예약 불가"}</td>
          <td>${escapeHtml(money(offer.member))}${offer.member_available ? "" : "<br>예약 불가"}</td></tr>`).join("")}</tbody></table>
        ${Number.isInteger(result.other_plans_count) ? `<p class="muted">기타 플랜 ${result.other_plans_count}건</p>` : ""}` : "";
      return `<div class="group-hotel"><div class="selected-heading"><a class="hotel-name" href="${url}" target="_blank" rel="noopener"><span>${name}</span><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7h10v10"/><path d="M7 17 17 7"/></svg></a>
        <button type="button" class="remove-hotel" data-group="${groupId}" data-hotel="${escapeHtml(code)}" aria-label="${name} 감시 대상에서 삭제" title="호텔 삭제">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button></div>
        <p class="hotel-status"><span class="status-label ${statusClass}">${escapeHtml(status)}</span><span class="muted">${checked}</span></p>
        ${result?.error ? `<p class="message">${escapeHtml(result.error)}</p>` : ""}${offers}</div>`;
    }).join("");
    return `<div class="monitor-group"><div class="group-header"><div class="selected-heading"><div>
      <h3 class="group-dates"><time datetime="${escapeHtml(group.start)}">${escapeHtml(group.start)}</time> <span aria-hidden="true">–</span> <time datetime="${escapeHtml(group.end)}">${escapeHtml(group.end)}</time></h3></div>
      <button type="button" class="remove-hotel" data-group="${groupId}" aria-label="${escapeHtml(group.start)} 일정 삭제" title="일정 전체 삭제">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button></div>
      <p class="group-conditions">${group.people}명 · ${escapeHtml(roomLabels[group.room_type] || group.room_type)} · ${escapeHtml(smokingLabels[group.smoking] || group.smoking)} · 학생 할인 ${group.student ? "적용" : "미적용"}</p></div>${list}</div>`;
  }).join("") : '<p class="muted">추가한 감시 대상이 없어요</p>';
}

$("results").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-group]");
  if (!button) return;
  button.disabled = true;
  const group = groups.find((item) => item.id === button.dataset.group);
  const removesGroup = group && (!button.dataset.hotel || group.hotels.length === 1);
  const path = '/api/groups/' + encodeURIComponent(button.dataset.group) +
    (button.dataset.hotel ? '/hotels/' + encodeURIComponent(button.dataset.hotel) : '');
  try {
    await api(path, {method: 'DELETE'});
    if (removesGroup && !$("start").value && !$("end").value) {
      $("start").value = group.start;
      $("end").value = group.end;
      $("draftNotice").textContent = "삭제한 일정의 날짜를 입력란에 넣었습니다.";
    }
    await refresh();
  } catch (error) {
    $("error").textContent = error.message;
    button.disabled = false;
  }
});

const seenNotifications = new Map();
function browserAlerts(s) {
  const c = s.config;
  if (!c.enabled || c.telegram_enabled || c.email_enabled) return;
  if (!("Notification" in window) || Notification.permission !== "granted") {
    $("status").textContent +=
      " · 브라우저 알림 권한이 필요해요, 권한 설정 후 감시를 다시 시작해 주세요";
    return;
  }
  for (const group of s.groups || []) {
  for (const [code, result] of Object.entries(group.results || {})) {
    const id = result.availability_id;
    if (!id || !result.offers?.length || result.status === "확인 오류")
      continue;
    const key = "toyoko-availability-" + group.id + "-" + code;
    let previous = seenNotifications.get(key);
    try {
      previous = localStorage.getItem(key) || previous;
    } catch (e) {}
    if (previous === id) continue;
    try {
      const notification = new Notification("토요코인 빈방 발견", {
        body: result.name + " · " + group.start + " ~ " + group.end,
        tag: key,
      });
      notification.onclick = () => {
        window.focus();
        notification.close();
      };
      seenNotifications.set(key, id);
      try {
        localStorage.setItem(key, id);
      } catch (e) {}
    } catch (e) {
      $("status").textContent += " · 브라우저 알림을 표시하지 못했어요.";
    }
  }
  }
}
async function refresh() {
  try {
    let s = await api("/api/state");
    rate = s.rate;
    if (rate)
      $("rate").textContent =
        "100엔 = " +
        (rate.value * 100).toLocaleString("ko-KR", {
          maximumFractionDigits: 2,
        }) +
        "원 · " +
        rate.date +
        " 기준";
    else
      $("rate").textContent =
        "환율을 불러오지 못했습니다. 엔화 가격만 표시합니다.";
    if (!initialized) {
      let c = s.config;
      for (let k of [
        "chat",
        "smtp_host",
        "smtp_port",
        "smtp_security",
        "smtp_user",
        "email_to",
      ])
        if (c[k] != null) $(k).value = c[k];
      $("telegram_enabled").checked = !!c.telegram_enabled;
      $("email_enabled").checked = !!c.email_enabled;
      showChannels();
      $("token").placeholder = c.token_set
        ? "저장됨 (변경 시 입력)"
        : "BotFather에서 발급";
      $("smtp_password").placeholder = c.smtp_password_set
        ? "저장됨 (변경 시 입력)"
        : "필요 시 앱 비밀번호";
      displayHotels();
      initialized = true;
    }
    $("status").textContent = "상태: " + s.status + (s.config.enabled ? " · " + s.interval_minutes + "분 간격" : "");
    browserAlerts(s);
    groups = s.groups || [];
    renderGroups();
  } catch (e) {
    $("status").textContent = "상태: 불러오기 실패";
  }
}
$("addGroup").onclick = async () => {
  const button = $("addGroup");
  button.disabled = true;
  $("draftError").textContent = "";
  $("draftNotice").textContent = "";
  try {
    const data = {};
    for (const key of ["start", "end", "people", "room_type"]) data[key] = $(key).value;
    data.smoking = $("smoking").checked ? "noSmoking" : "all";
    data.student = $("student").checked;
    data.hotels = [...selected];
    await api("/api/groups", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
    selected.clear();
    $("start").value = "";
    $("end").value = "";
    $("people").value = "1";
    $("room_type").value = "all";
    $("smoking").checked = false;
    $("student").checked = false;
    $("filter").value = "";
    $("place").value = "";
    $("radius").value = "5";
    $("map").hidden = true;
    $("mapSource").hidden = true;
    hotelMarkers.clear();
    shown = hotels;
    $("placeNote").textContent = "한국어, 일본어, 영어로 장소를 검색할 수 있습니다.";
    displayHotels();
    await refresh();
  } catch (error) {
    $("draftError").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
$("startBtn").onclick = async () => {
  try {
    $("error").textContent = "";
    let data = {};
    for (let k of [
      "start",
      "end",
      "people",
      "room_type",
      "smoking",
      "token",
      "chat",
      "smtp_host",
      "smtp_port",
      "smtp_security",
      "smtp_user",
      "smtp_password",
      "email_to",
    ])
      data[k] = $(k).value;
    if (!groups.length) throw Error("감시할 조건을 먼저 추가해 주세요.");
    data.telegram_enabled = $("telegram_enabled").checked;
    data.email_enabled = $("email_enabled").checked;
    if (!data.telegram_enabled && !data.email_enabled) {
      if (!("Notification" in window))
        throw Error("이 브라우저는 알림을 지원하지 않습니다.");
      let permission = Notification.permission;
      if (permission === "default")
        permission = await Notification.requestPermission();
      if (permission !== "granted")
        throw Error(
          "브라우저 알림이 차단되어 있습니다. 브라우저 설정에서 알림을 허용해 주세요.",
        );
    }
    await api("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    $("token").value = "";
    $("smtp_password").value = "";
    $("token").placeholder = "저장됨 (변경 시 입력)";
    $("smtp_password").placeholder = "저장됨 (변경 시 입력)";
    refresh();
  } catch (e) {
    $("error").textContent = e.message;
  }
};
$("stopBtn").onclick = async () => {
  try {
    await api("/api/stop", { method: "POST" });
    refresh();
  } catch (e) {
    $("error").textContent = e.message;
  }
};
(async () => {
  let d = new Date(),
    n = new Date(d);
  n.setDate(n.getDate() + 1);
  const local = (x) =>
    [
      x.getFullYear(),
      String(x.getMonth() + 1).padStart(2, "0"),
      String(x.getDate()).padStart(2, "0"),
    ].join("-");
  $("start").value = local(d);
  $("end").value = local(n);
  try {
    hotels = await api("/api/hotels");
    shown = hotels;
    displayHotels();
  } catch (e) {
    $("hotels").textContent = e.message;
  }
  await api("/api/rate").catch(() => {});
  await refresh();
  setInterval(refresh, 5000);
})();

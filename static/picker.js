// static/picker.js — הבורר (שלב 2): חיפוש לוקיישן, דפדוף בתתי-התיקיות שלו, ובחירה של עד 12 תמונות.
//
// המסך נקבע רק לפי הכתובת, כדי שכפתור "אחורה" של הטלפון יעבוד בין המסכים:
//   #/                  חיפוש
//   #/s/<טקסט>          חיפוש עם טקסט (כדי שחזרה מלוקיישן תציג שוב את התוצאות)
//   #/l/<מיקום>/<תיקייה>/...   לוקיישן: המזהה הראשון הוא תיקיית הלוקיישן, והשאר הנתיב בתוכו
//
// הבחירה נשמרת בין תתי-תיקיות של אותו לוקיישן, ומתאפסת רק במעבר ללוקיישן אחר (ROADMAP).
// היא נשמרת גם ב-sessionStorage, כדי שרענון הדף לא ימחק אותה.
//
// כל טקסט שמגיע מדרייב (שמות תיקיות וקבצים) נכנס לדף עם textContent בלבד.
(function () {
  "use strict";

  var root = document.getElementById("picker");
  if (!root) { return; }

  var MAX = parseInt(root.getAttribute("data-max"), 10) || 12;
  var STORAGE_KEY = "images2pdf.picker.v1";
  var SEARCH_DEBOUNCE_MS = 400;
  var MIN_QUERY_CHARS = 2;
  var ID_RE = /^[A-Za-z0-9_-]{1,200}$/;

  function $(id) { return document.getElementById(id); }
  var el = {
    screenSearch: $("screen-search"),
    screenLocation: $("screen-location"),
    searchForm: $("search-form"),
    searchInput: $("search-input"),
    searchStatus: $("search-status"),
    searchResults: $("search-results"),
    searchMore: $("search-more"),
    back: $("back-button"),
    heading: $("location-heading"),
    breadcrumb: $("breadcrumb"),
    locationStatus: $("location-status"),
    grid: $("grid"),
    selection: $("selection"),
    selectionCount: $("selection-count"),
    clearSelection: $("clear-selection"),
    strip: $("selection-strip"),
    title: $("title-input"),
    toast: $("toast")
  };

  // ─── מצב ────────────────────────────────────────────────────

  function emptyState() {
    return { location: null, selection: [], title: "", titleEdited: false, names: {} };
  }

  function isItem(s) {
    return s && typeof s === "object" && ID_RE.test(s.id) && typeof s.name === "string" &&
      (s.thumb === null || typeof s.thumb === "string");
  }

  function validState(s) {
    if (!s || typeof s !== "object") { return false; }
    if (s.location !== null && !(s.location && ID_RE.test(s.location.id) && typeof s.location.name === "string")) { return false; }
    if (!Array.isArray(s.selection) || s.selection.length > MAX || !s.selection.every(isItem)) { return false; }
    if (typeof s.title !== "string" || typeof s.titleEdited !== "boolean") { return false; }
    return s.names && typeof s.names === "object";
  }

  function loadState() {
    try {
      var raw = window.sessionStorage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return validState(parsed) ? parsed : null;
    } catch (e) {
      return null; // אחסון חסום או תוכן פגום: מתחילים נקי
    }
  }

  function saveState() {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // גלישה פרטית או אחסון מלא: הבחירה נשמרת רק בזיכרון, עד רענון הדף.
    }
  }

  var state = loadState() || emptyState();

  function rememberName(id, name) {
    if (!ID_RE.test(id) || typeof name !== "string" || !name) { return; }
    state.names[id] = name;
    var keys = Object.keys(state.names);
    if (keys.length > 300) { delete state.names[keys[0]]; } // תקרה כדי שהאחסון לא יגדל בלי סוף
  }

  // ─── קריאות לשרת ────────────────────────────────────────────

  function api(url, signal) {
    return fetch(url, { credentials: "same-origin", signal: signal, headers: { "Accept": "application/json" } })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          if (!resp.ok) {
            var err = new Error("request failed");
            err.code = body && typeof body.error === "string" ? body.error : "http_" + resp.status;
            throw err;
          }
          return body;
        });
      });
  }

  function setStatus(node, text, link) {
    node.textContent = "";
    if (!text) { return; }
    node.appendChild(document.createTextNode(text));
    if (link) {
      var a = document.createElement("a");
      a.href = link.href;
      a.textContent = link.text;
      node.appendChild(document.createTextNode(" "));
      node.appendChild(a);
    }
  }

  function showError(node, err) {
    if (err.code === "unauthorized") {
      window.location.href = "/login?next=" + encodeURIComponent("/" + window.location.hash);
      return;
    }
    if (err.code === "google_reconnect_required") {
      setStatus(node, "צריך לחבר מחדש את חשבון הגוגל.", { href: "/settings", text: "למסך החיבור" });
    } else if (err.code === "not_found" || err.code === "bad_folder_id") {
      setStatus(node, "התיקייה לא נמצאה, או שהחשבון לא רואה אותה.");
    } else if (err.code === "query_too_long") {
      setStatus(node, "הטקסט לחיפוש ארוך מדי.");
    } else {
      setStatus(node, "משהו השתבש מול גוגל. נסו שוב בעוד רגע.");
    }
  }

  var toastTimer = null;
  function toast(text) {
    el.toast.textContent = text;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.hidden = true; }, 4000);
  }

  // ─── ניתוב ──────────────────────────────────────────────────

  function safeDecode(text) {
    try { return decodeURIComponent(text); } catch (e) { return ""; }
  }

  function parseHash() {
    var parts = window.location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] === "l") {
      var ids = parts.slice(1).filter(function (p) { return p !== ""; });
      if (ids.length > 0 && ids.every(function (id) { return ID_RE.test(id); })) {
        return { screen: "location", path: ids };
      }
    }
    if (parts[0] === "s") {
      return { screen: "search", q: safeDecode(parts.slice(1).join("/")) };
    }
    return { screen: "search", q: "" };
  }

  function locationHash(path) { return "#/l/" + path.join("/"); }
  function searchHash(q) { return q ? "#/s/" + encodeURIComponent(q) : "#/"; }

  function go(hash) {
    if (window.location.hash === hash) { route(); } else { window.location.hash = hash; }
  }

  function replaceHash(hash) {
    if (window.location.hash !== hash) { window.history.replaceState(null, "", hash); }
  }

  // הכתובת של המסך שמוצג עכשיו — לשם חוזרים כשהמשתמש מבטל מעבר ללוקיישן אחר.
  var shownHash = null;

  // הבחירה שייכת ללוקיישן אחד, ומתאפסת רק במעבר ללוקיישן אחר (ROADMAP). המעבר יכול
  // להגיע מתוצאת חיפוש, מכפתור "אחורה" או "קדימה" של הדפדפן, או מכתובת שהודבקה —
  // ולכן האישור נמצא כאן, בנקודה שכל המסלולים עוברים בה, ולא רק בלחיצה על תוצאה.
  function confirmSwitch(locId) {
    var n = state.selection.length;
    if (!state.location || state.location.id === locId || n === 0) { return true; }
    var from = state.location.name ? "מ\"" + state.location.name + "\" " : "";
    return window.confirm(n === 1
      ? "לעבור ללוקיישן אחר? התמונה שנבחרה " + from + "תימחק מהבחירה."
      : "לעבור ללוקיישן אחר? " + n + " התמונות שנבחרו " + from + "יימחקו מהבחירה.");
  }

  function switchLocation(locId) {
    if (state.location && state.location.id === locId) { return; }
    state.location = { id: locId, name: nameOf(locId) };
    state.selection = [];
    state.title = nameOf(locId);
    state.titleEdited = false;
    saveState();
  }

  function route() {
    var r = parseHash();
    if (r.screen === "location") {
      if (!confirmSwitch(r.path[0])) {
        // ביטול: הכתובת חוזרת למסך שמוצג, בלי לגעת בבחירה. replaceState לא מפעיל hashchange.
        replaceHash(shownHash || locationHash([state.location.id]));
        if (shownHash === null) { route(); } // טעינה ראשונה: עדיין לא הוצג מסך, מציגים את הלוקיישן השמור
        return;
      }
      switchLocation(r.path[0]);
      showLocation(r.path);
    } else {
      showSearch(r.q);
    }
    shownHash = window.location.hash || "#/";
  }

  // ─── מסך החיפוש ────────────────────────────────────────────

  var search = { seq: 0, abort: null, query: "", token: null, timer: null };

  function normalize(text) { return text.split(/\s+/).filter(Boolean).join(" "); }

  function showSearch(q) {
    cancelListing();
    el.screenLocation.hidden = true;
    el.selection.hidden = true;
    el.screenSearch.hidden = false;
    root.classList.remove("on-location");
    document.title = "חיפוש לוקיישן — כלי PDF ללוקיישנים";
    if (q && normalize(el.searchInput.value) !== q) { el.searchInput.value = q; }
    if (q && q !== search.query) { runSearch(q, null); }
  }

  function runSearch(q, token) {
    var seq = ++search.seq;
    if (search.abort) { search.abort.abort(); }
    search.abort = new AbortController();
    if (!token) {
      search.query = q;
      el.searchResults.textContent = "";
    }
    el.searchMore.hidden = true;
    setStatus(el.searchStatus, "מחפש…");
    var url = "/api/drive/search?q=" + encodeURIComponent(q) + (token ? "&page_token=" + encodeURIComponent(token) : "");
    api(url, search.abort.signal).then(function (data) {
      if (seq !== search.seq) { return; } // תשובה של חיפוש קודם שהגיעה באיחור
      (Array.isArray(data.results) ? data.results : []).forEach(renderResult);
      var total = el.searchResults.children.length;
      search.token = typeof data.nextPageToken === "string" && data.nextPageToken ? data.nextPageToken : null;
      var text;
      if (total === 0) {
        text = "לא נמצאו תיקיות שמתחילות ב-\"" + q + "\". החיפוש מוצא תחילת מילה, אז כדאי לנסות את תחילת השם.";
      } else {
        var found = total === 1 ? "נמצאה תיקייה אחת" : "נמצאו " + total + " תיקיות";
        text = search.token ? found + ", ויש עוד." : found + ".";
      }
      // incompleteSearch של דרייב: לא כל המקומות נסרקו, ולכן ייתכן שחסרות תוצאות.
      if (data.incomplete === true) { text += " גוגל לא הספיק לחפש בכל המקומות, אז ייתכן שחסרות תוצאות. אפשר לחפש שוב."; }
      setStatus(el.searchStatus, text);
      el.searchMore.hidden = !search.token;
    }).catch(function (err) {
      if (err.name === "AbortError" || seq !== search.seq) { return; }
      showError(el.searchStatus, err);
    });
  }

  function renderResult(r) {
    if (!r || !ID_RE.test(r.id) || typeof r.name !== "string") { return; }
    var li = document.createElement("li");
    var button = document.createElement("button");
    button.type = "button";
    button.className = "result";
    var name = document.createElement("span");
    name.className = "result-name";
    name.dir = "auto";
    name.textContent = r.name;
    button.appendChild(name);
    if (r.parent && typeof r.parent.name === "string") {
      var parent = document.createElement("span");
      parent.className = "result-parent";
      parent.textContent = "בתוך: " + r.parent.name;
      button.appendChild(parent);
    }
    button.addEventListener("click", function () { openLocation(r.id, r.name); });
    li.appendChild(button);
    el.searchResults.appendChild(li);
  }

  function openLocation(id, name) {
    rememberName(id, name);
    if (!confirmSwitch(id)) { return; }
    switchLocation(id);
    go(locationHash([id]));
  }

  el.searchInput.addEventListener("input", function () {
    clearTimeout(search.timer);
    var q = normalize(el.searchInput.value);
    if (q.length < MIN_QUERY_CHARS) {
      search.seq++;
      if (search.abort) { search.abort.abort(); }
      search.query = "";
      el.searchResults.textContent = "";
      el.searchMore.hidden = true;
      setStatus(el.searchStatus, "");
      replaceHash("#/");
      return;
    }
    search.timer = setTimeout(function () {
      replaceHash(searchHash(q));
      runSearch(q, null);
    }, SEARCH_DEBOUNCE_MS);
  });

  el.searchForm.addEventListener("submit", function (event) {
    event.preventDefault();
    clearTimeout(search.timer);
    var q = normalize(el.searchInput.value);
    if (!q) { return; }
    replaceHash(searchHash(q));
    runSearch(q, null);
  });

  el.searchMore.addEventListener("click", function () {
    if (search.token) { runSearch(search.query, search.token); }
  });

  // ─── מסך הלוקיישן ──────────────────────────────────────────

  var listing = { seq: 0, abort: null, path: [] };

  function cancelListing() {
    listing.seq++;
    if (listing.abort) { listing.abort.abort(); }
  }

  function nameOf(id) { return state.names[id] || ""; }

  function fetchName(id) {
    api("/api/drive/folder/" + id).then(function (data) {
      if (typeof data.name === "string") {
        rememberName(id, data.name);
        if (state.location && state.location.id === id) {
          state.location.name = data.name;
          if (!state.titleEdited) { state.title = data.name; }
        }
        saveState();
        renderHeader();
        renderSelection();
      }
    }).catch(function (err) {
      showError(el.locationStatus, err);
    });
  }

  function showLocation(path) {
    listing.path = path;
    saveState();
    path.forEach(function (id) { if (!nameOf(id)) { fetchName(id); } });

    el.screenSearch.hidden = true;
    el.screenLocation.hidden = false;
    el.selection.hidden = false;
    root.classList.add("on-location");
    renderHeader();
    renderSelection();
    loadFolder(path[path.length - 1]);
  }

  function renderHeader() {
    var path = listing.path;
    if (!path.length) { return; }
    el.heading.textContent = nameOf(path[0]) || "…";
    document.title = (nameOf(path[0]) || "לוקיישן") + " — בחירת תמונות";
    el.breadcrumb.textContent = "";
    if (path.length < 2) { return; } // בשורש הלוקיישן אין מה להציג מעבר לכותרת
    path.forEach(function (id, i) {
      if (i > 0) {
        var sep = document.createElement("span");
        sep.className = "crumb-sep";
        sep.setAttribute("aria-hidden", "true");
        sep.textContent = "/";
        el.breadcrumb.appendChild(sep);
      }
      var label = nameOf(id) || "…";
      if (i === path.length - 1) {
        var current = document.createElement("span");
        current.className = "crumb-current";
        current.setAttribute("aria-current", "page");
        current.textContent = label;
        el.breadcrumb.appendChild(current);
      } else {
        var link = document.createElement("button");
        link.type = "button";
        link.className = "crumb";
        link.textContent = label;
        link.addEventListener("click", function () { go(locationHash(path.slice(0, i + 1))); });
        el.breadcrumb.appendChild(link);
      }
    });
  }

  el.back.addEventListener("click", function () {
    var path = listing.path;
    if (path.length > 1) { go(locationHash(path.slice(0, -1))); } else { go(searchHash(search.query)); }
  });

  function loadFolder(folderId) {
    cancelListing();
    var seq = listing.seq;
    listing.abort = new AbortController();
    var signal = listing.abort.signal;
    el.grid.textContent = "";
    setStatus(el.locationStatus, "טוען…");
    var counts = { folders: 0, images: 0 };

    function page(token) {
      var url = "/api/drive/files?folder_id=" + folderId + (token ? "&page_token=" + encodeURIComponent(token) : "");
      return api(url, signal).then(function (data) {
        if (seq !== listing.seq) { return; } // המשתמש כבר עבר לתיקייה אחרת
        (Array.isArray(data.folders) ? data.folders : []).forEach(function (f) {
          if (f && ID_RE.test(f.id) && typeof f.name === "string") {
            el.grid.appendChild(folderTile(f));
            counts.folders++;
          }
        });
        (Array.isArray(data.images) ? data.images : []).forEach(function (img) {
          if (img && ID_RE.test(img.id) && typeof img.name === "string") {
            el.grid.appendChild(imageTile(img));
            counts.images++;
          }
        });
        if (typeof data.nextPageToken === "string" && data.nextPageToken) {
          setStatus(el.locationStatus, "טוען… " + counts.images + " תמונות עד עכשיו");
          return page(data.nextPageToken);
        }
        setStatus(el.locationStatus, summary(counts));
      });
    }

    page(null).catch(function (err) {
      if (err.name === "AbortError" || seq !== listing.seq) { return; }
      showError(el.locationStatus, err);
    });
  }

  function summary(counts) {
    if (!counts.folders && !counts.images) { return "אין כאן תמונות או תיקיות."; }
    var parts = [];
    if (counts.folders) { parts.push(counts.folders === 1 ? "תת-תיקייה אחת" : counts.folders + " תתי-תיקיות"); }
    if (counts.images) { parts.push(counts.images === 1 ? "תמונה אחת" : counts.images + " תמונות"); }
    return parts.join(" · ");
  }

  function folderTile(f) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tile tile-folder";
    var icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.setAttribute("aria-hidden", "true");
    var name = document.createElement("span");
    name.className = "tile-name";
    name.dir = "auto";
    name.textContent = f.name;
    button.appendChild(icon);
    button.appendChild(name);
    button.setAttribute("aria-label", "תיקייה: " + f.name);
    button.addEventListener("click", function () {
      rememberName(f.id, f.name);
      go(locationHash(listing.path.concat([f.id])));
    });
    return button;
  }

  function thumbImage(id, link) {
    // קודם הקישור הישיר של גוגל; אם התמונה לא נטענת — הנתיב שדרך השרת, רק לתמונה הזו.
    var img = document.createElement("img");
    var proxy = "/api/drive/thumbnail/" + id;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.src = typeof link === "string" && link.indexOf("https://") === 0 ? link : proxy;
    img.addEventListener("error", function onError() {
      if (img.getAttribute("src") !== proxy) {
        img.src = proxy;
        return;
      }
      img.removeEventListener("error", onError);
      img.classList.add("thumb-broken");
    });
    return img;
  }

  // שם התמונה בלי הסיומת. עם הסיומת, שם עברי כמו "גינה 11.jpg" מוצג בכיוון עברי
  // כ-"jpg.11 גינה", ובכיוון לועזי כ-"11 גינה.jpg" — לפי האלגוריתם הדו-כיווני. בגלריה
  // אין צורך בסיומת, ובלעדיה השם מוצג נכון.
  function displayName(name) {
    return name.replace(/\.(jpe?g|png)$/i, "") || name;
  }

  function imageTile(item) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tile tile-image";
    button.setAttribute("data-id", item.id);
    button.setAttribute("data-name", item.name);
    button.appendChild(thumbImage(item.id, item.thumbnailLink));
    var name = document.createElement("span");
    name.className = "tile-name";
    name.dir = "auto";
    name.textContent = displayName(item.name);
    button.appendChild(name);
    var badge = document.createElement("span");
    badge.className = "order-badge";
    badge.setAttribute("aria-hidden", "true");
    button.appendChild(badge);
    button.addEventListener("click", function () { toggle(item); });
    syncTile(button);
    return button;
  }

  function indexInSelection(id) {
    for (var i = 0; i < state.selection.length; i++) {
      if (state.selection[i].id === id) { return i; }
    }
    return -1;
  }

  function syncTile(button) {
    var i = indexInSelection(button.getAttribute("data-id"));
    var name = button.getAttribute("data-name");
    button.classList.toggle("selected", i >= 0);
    button.setAttribute("aria-pressed", i >= 0 ? "true" : "false");
    var label = displayName(name);
    button.setAttribute("aria-label", i >= 0 ? label + ", נבחרה מספר " + (i + 1) : label);
    button.querySelector(".order-badge").textContent = i >= 0 ? String(i + 1) : "";
  }

  function syncTiles() {
    Array.prototype.forEach.call(el.grid.querySelectorAll(".tile-image"), syncTile);
  }

  function toggle(item) {
    var i = indexInSelection(item.id);
    if (i >= 0) {
      state.selection.splice(i, 1);
    } else {
      if (state.selection.length >= MAX) {
        toast("אפשר לבחור עד " + MAX + " תמונות. כדי להוסיף, מסירים קודם תמונה אחרת.");
        return;
      }
      state.selection.push({
        id: item.id,
        name: item.name,
        thumb: typeof item.thumbnailLink === "string" ? item.thumbnailLink : null,
        folderId: listing.path[listing.path.length - 1]
      });
    }
    saveState();
    syncTiles();
    renderSelection();
  }

  // ─── הבחירה והכותרת ────────────────────────────────────────

  function renderSelection() {
    var n = state.selection.length;
    el.selectionCount.textContent = n === 0 ? "לא נבחרו תמונות" : "נבחרו " + n + " מתוך " + MAX;
    el.clearSelection.hidden = n === 0;
    el.strip.textContent = "";
    state.selection.forEach(function (item, i) {
      var li = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "strip-item";
      button.setAttribute("aria-label", "הסרת תמונה " + (i + 1) + ": " + displayName(item.name));
      button.title = displayName(item.name);
      button.appendChild(thumbImage(item.id, item.thumb));
      var badge = document.createElement("span");
      badge.className = "order-badge";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = String(i + 1);
      button.appendChild(badge);
      button.addEventListener("click", function () {
        state.selection.splice(i, 1);
        saveState();
        syncTiles();
        renderSelection();
      });
      li.appendChild(button);
      el.strip.appendChild(li);
    });
    if (document.activeElement !== el.title) { el.title.value = state.title; }
  }

  el.clearSelection.addEventListener("click", function () {
    var n = state.selection.length;
    if (n > 1 && !window.confirm("לנקות את כל " + n + " התמונות שנבחרו?")) { return; }
    state.selection = [];
    saveState();
    syncTiles();
    renderSelection();
  });

  el.title.addEventListener("input", function () {
    state.title = el.title.value;
    state.titleEdited = true;
    saveState();
  });

  // ─── התחלה ─────────────────────────────────────────────────

  window.addEventListener("hashchange", route);
  route();
})();

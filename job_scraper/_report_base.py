"""Shared base CSS and JS for HTML reports."""

from datetime import UTC, datetime

BASE_CSS = """\
:root {
  --bg: #f5f5f5; --fg: #111; --table-bg: white;
  --border: #eee; --th-bg: #fafafa;
  --hover: #f9f9f9; --link: #0066cc;
  --muted: #777;
  --badge-green-bg: #d4edda;
  --badge-green-fg: #155724;
  --badge-yellow-bg: #fff3cd;
  --badge-yellow-fg: #856404;
  --badge-red-bg: #f8d7da;
  --badge-red-fg: #721c24;
  --btn-bg: white; --btn-border: #ccc;
  --panel-bg: white; --panel-border: #ddd;
  --panel-hover: #f5f5f5;
  --shadow: 0 1px 3px rgba(0,0,0,0.1);
  --panel-shadow: 0 4px 12px rgba(0,0,0,0.12);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1a1a; --fg: #e0e0e0;
    --table-bg: #222; --border: #333;
    --th-bg: #2a2a2a; --hover: #2a2a3a;
    --link: #6cacee; --muted: #999;
    --badge-green-bg: #1b4332;
    --badge-green-fg: #66bb6a;
    --badge-yellow-bg: #3e2c0a;
    --badge-yellow-fg: #ffc107;
    --badge-red-bg: #3c1111;
    --badge-red-fg: #ef5350;
    --btn-bg: #2a2a2a; --btn-border: #555;
    --panel-bg: #2a2a2a; --panel-border: #444;
    --panel-hover: #333;
    --shadow: 0 1px 3px rgba(0,0,0,0.3);
    --panel-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: system-ui, sans-serif;
  background: var(--bg); color: var(--fg);
  padding: 2rem; }
h1 { margin-bottom: 0.3rem; font-size: 1.5rem; }
.meta { font-size: 0.85em; color: var(--muted);
  margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse;
  background: var(--table-bg); border-radius: 8px;
  overflow: hidden; box-shadow: var(--shadow);
  font-size: 0.85em; }
th, td { padding: 0.4rem 0.6rem; text-align: left;
  border-bottom: 1px solid var(--border); }
th { background: var(--th-bg); font-weight: 600;
  position: sticky; top: 0; cursor: pointer;
  user-select: none; white-space: nowrap; }
th::after { content: ""; display: inline-block;
  width: 0.6em; margin-left: 0.3em; }
th.sort-asc::after { content: "\\25B2";
  font-size: 0.6em; vertical-align: middle; }
th.sort-desc::after { content: "\\25BC";
  font-size: 0.6em; vertical-align: middle; }
tr:hover { background: var(--hover); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
.num { text-align: right;
  font-variant-numeric: tabular-nums; }
.badge { font-weight: 700; padding: 0.15rem 0.35rem;
  border-radius: 4px; display: inline-block;
  text-align: center; }
.badge-green { background: var(--badge-green-bg);
  color: var(--badge-green-fg); }
.badge-yellow { background: var(--badge-yellow-bg);
  color: var(--badge-yellow-fg); }
.badge-red { background: var(--badge-red-bg);
  color: var(--badge-red-fg); }
.col-toggle { position: relative;
  display: inline-block; }
.btn { font: inherit; font-size: 0.85em;
  padding: 0.35rem 0.7rem;
  background: var(--btn-bg);
  border: 1px solid var(--btn-border);
  border-radius: 6px; cursor: pointer;
  color: var(--fg); }
.btn:hover:not(:disabled) {
  background: var(--panel-hover); }
.btn:disabled { opacity: 0.4; cursor: default; }
.col-panel { display: none; position: absolute;
  left: 0; top: 100%; margin-top: 0.3rem;
  z-index: 20; background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  box-shadow: var(--panel-shadow);
  padding: 0.5rem 0; min-width: 150px; }
.col-panel.open { display: block; }
.col-panel label { display: block;
  padding: 0.25rem 0.75rem; font-size: 0.85em;
  cursor: pointer; white-space: nowrap; }
.col-panel label:hover {
  background: var(--panel-hover); }
.pager { display: flex; align-items: center;
  gap: 0.5rem; font-size: 0.85em; }
"""

BASE_JS = """\
(function() {
  var el = document.getElementById('generated-at');
  if (el) {
    var dt = el.getAttribute('datetime');
    el.textContent =
      new Date(dt).toLocaleString();
  }
})();
document.addEventListener('click', function(e) {
  var t = document.querySelector('.col-toggle');
  if (t && !t.contains(e.target)) {
    document.getElementById('col-panel')
      .classList.remove('open');
  }
});
function initColumns(cols, key) {
  var saved = null;
  try {
    saved = JSON.parse(
      localStorage.getItem(key));
  } catch(e) {}
  if (saved && typeof saved === 'object') {
    cols.forEach(function(c) {
      if (saved.hasOwnProperty(c.name))
        c.on = saved[c.name];
    });
  }
  var st = document.createElement('style');
  document.head.appendChild(st);
  function apply() {
    var rules = [], state = {};
    cols.forEach(function(c, i) {
      state[c.name] = c.on;
      if (!c.on) {
        var n = i + 1;
        rules.push(
          'th:nth-child(' + n
          + '),td:nth-child(' + n
          + '){display:none}');
      }
    });
    st.textContent = rules.join('');
    try {
      localStorage.setItem(
        key, JSON.stringify(state));
    } catch(e) {}
  }
  var panel = document.getElementById('col-panel');
  cols.forEach(function(c) {
    var lbl = document.createElement('label');
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = c.on;
    cb.addEventListener('change', function() {
      c.on = cb.checked; apply();
    });
    lbl.appendChild(cb);
    lbl.appendChild(
      document.createTextNode(' ' + c.name));
    panel.appendChild(lbl);
  });
  document.getElementById('col-btn')
    .addEventListener('click', function(e) {
      panel.classList.toggle('open');
      e.stopPropagation();
    });
  apply();
}
function initPager(pageSize) {
  var curPage = 0;
  var pgPrev = document.getElementById('pg-prev');
  var pgNext = document.getElementById('pg-next');
  var pgInfo = document.getElementById('pg-info');
  var pager = document.getElementById('pager');
  var tbody = document.querySelector('tbody');
  function getRows() {
    return Array.from(
      tbody.querySelectorAll(
        'tr:not(.search-hide)'));
  }
  function showPage(page) {
    var rows = getRows();
    var total = rows.length;
    var numPages = Math.max(1,
      Math.ceil(total / pageSize));
    curPage = Math.max(0,
      Math.min(page, numPages - 1));
    var start = curPage * pageSize;
    var end = start + pageSize;
    rows.forEach(function(r, i) {
      r.style.display =
        (i >= start && i < end) ? '' : 'none';
    });
    pgInfo.textContent = (start + 1)
      + '\\u2013' + Math.min(end, total)
      + ' of ' + total;
    pgPrev.disabled = curPage === 0;
    pgNext.disabled = curPage >= numPages - 1;
    pager.style.display =
      numPages <= 1 ? 'none' : '';
  }
  pgPrev.addEventListener('click', function() {
    showPage(curPage - 1);
  });
  pgNext.addEventListener('click', function() {
    showPage(curPage + 1);
  });
  return showPage;
}
function initSort(afterSort) {
  var table = document.querySelector('table');
  var tbody = table.querySelector('tbody');
  var ths = table.querySelectorAll('thead th');
  ths.forEach(function(th, col) {
    th.addEventListener('click', function() {
      var desc = th.hasAttribute(
        'data-sort-desc');
      var asc = desc
        ? th.classList.contains('sort-desc')
        : !th.classList.contains('sort-asc');
      ths.forEach(function(h) {
        h.classList.remove(
          'sort-asc', 'sort-desc');
      });
      th.classList.add(
        asc ? 'sort-asc' : 'sort-desc');
      var rows = Array.from(
        tbody.querySelectorAll('tr'));
      rows.sort(function(a, b) {
        var ac = a.children[col];
        var bc = b.children[col];
        var av = ac.dataset.sort != null
          ? ac.dataset.sort
          : ac.textContent.trim();
        var bv = bc.dataset.sort != null
          ? bc.dataset.sort
          : bc.textContent.trim();
        var an = parseFloat(av);
        var bn = parseFloat(bv);
        var cmp = (!isNaN(an) && !isNaN(bn))
          ? an - bn
          : av.localeCompare(bv);
        return asc ? cmp : -cmp;
      });
      rows.forEach(function(r) {
        tbody.appendChild(r);
      });
      if (afterSort) afterSort();
    });
  });
}
function initSearch(afterFilter) {
  var input = document.getElementById('search');
  if (!input) return;
  var ths = document.querySelectorAll('thead th');
  var cols = [];
  ths.forEach(function(th, i) {
    if (th.hasAttribute('data-search'))
      cols.push(i);
  });
  var tbody = document.querySelector('tbody');
  var rows = Array.from(
    tbody.querySelectorAll('tr'));
  var texts = rows.map(function(r) {
    return cols.map(function(c) {
      var cell = r.children[c];
      var s = '';
      cell.childNodes.forEach(function(n) {
        if (n.nodeType === 3) s += n.data;
        else if (n.tagName === 'A')
          s += n.textContent;
      });
      return s;
    }).join(' ').toLowerCase();
  });
  input.addEventListener('input', function() {
    var parts = input.value.toLowerCase()
      .split(/\\s+/).filter(Boolean);
    rows.forEach(function(r, i) {
      var hide = parts.length > 0
        && !parts.every(function(p) {
          return texts[i].indexOf(p) >= 0;
        });
      r.classList.toggle('search-hide', hide);
    });
    if (afterFilter) afterFilter();
  });
}
"""


def _time_ago(date_str: str | None) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str).replace(
            tzinfo=UTC
        )
    except ValueError:
        return date_str
    delta = datetime.now(UTC) - dt
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        n = max(seconds // 60, 1)
        return f"{n} min ago" if n == 1 else f"{n} mins ago"
    if seconds < 86400:
        n = seconds // 3600
        return (
            f"{n} hour ago" if n == 1 else f"{n} hours ago"
        )
    days = seconds // 86400
    if days < 7:
        return (
            f"{days} day ago"
            if days == 1
            else f"{days} days ago"
        )
    if days < 30:
        n = days // 7
        return (
            f"{n} week ago" if n == 1 else f"{n} weeks ago"
        )
    if days < 365:
        n = days // 30
        return (
            f"{n} month ago"
            if n == 1
            else f"{n} months ago"
        )
    n = days // 365
    return f"{n} year ago" if n == 1 else f"{n} years ago"


def _epoch(date_str: str | None) -> int:
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(date_str).replace(
            tzinfo=UTC
        )
        return int(dt.timestamp())
    except ValueError:
        return 0

import json
import re
import urllib.parse
import webbrowser
import audible

AUTH_FILE = "audible_session.json"
COUNTRY_CODE = "us"
OUTPUT_HTML = "audible_library.html"

def get_audible_client():
    """Authenticate and return an active Audible API client session."""
    try:
        auth = audible.Authenticator.from_file(AUTH_FILE)
        return audible.Client(auth=auth)
    except FileNotFoundError:
        print("=== First-time Audible Login Setup ===")
        auth = audible.Authenticator.from_login_external(locale=COUNTRY_CODE)
        auth.to_file(AUTH_FILE)
        return audible.Client(auth=auth)

def extract_cover(item):
    """Safely extract cover image URL from Audible API response structures."""
    if not isinstance(item, dict):
        return ""
        
    image_sources = [
        item.get("product_images", {}),
        item.get("product_desc", {}).get("product_images", {}),
        item.get("product_attrs", {}).get("product_images", {}),
        item.get("sample_product", {}).get("product_images", {})
    ]
    
    for images in image_sources:
        if isinstance(images, dict) and images:
            for key in ["500", "1000", "250", "100", "large", "medium", "small"]:
                if key in images and images[key]:
                    return images[key]
            for val in images.values():
                if isinstance(val, str) and val.startswith("http"):
                    return val
                elif isinstance(val, dict):
                    for sub_val in val.values():
                        if isinstance(sub_val, str) and sub_val.startswith("http"):
                            return sub_val
                            
    return ""

def get_fallback_cover(title):
    """Fallback cover generator using Open Library Covers API for missing store books."""
    clean_title = re.sub(r'\(.*?\)', '', title).strip()
    encoded_title = urllib.parse.quote(clean_title)
    return f"https://covers.openlibrary.org/b/title/{encoded_title}-M.jpg?default=false"

def parse_omnibus_ranges(text):
    """Detect if a title represents a box set / omnibus range (e.g., 'Books 1-10', '1-3')."""
    covered_sequences = set()
    if not text:
        return covered_sequences
        
    patterns = [
        r"(?:books?|volumes?|parts?|trilogy|collection|box\s*set|set)?\s*(\d+)\s*(?:-|–|to|through|and)\s*(\d+)",
        r"(\d+)\s*(?:-|–)\s*(\d+)",
        r"(?:complete|boxset|trilogy|collection|omnibus).*?(\d+)\s*(?:-|–|to)\s*(\d+)"
    ]
    
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        for start, end in matches:
            try:
                s_int = int(start)
                e_int = int(end)
                if s_int <= e_int and (e_int - s_int) < 50:
                    for seq in range(s_int, e_int + 1):
                        covered_sequences.add(seq)
            except ValueError:
                continue
                
    return covered_sequences

def is_foreign_language(product):
    """Strictly detect and filter out non-English editions."""
    for key in ["language", "locale", "publication_language"]:
        val = product.get(key, "")
        if isinstance(val, str) and val:
            if "english" not in val.lower() and val.lower() != "en":
                return True
                
    p_desc = product.get("product_desc", {})
    if isinstance(p_desc, dict):
        for key in ["language", "locale"]:
            val = p_desc.get(key, "")
            if isinstance(val, str) and val:
                if "english" not in val.lower() and val.lower() != "en":
                    return True

    title = (product.get("title") or product.get("product_desc", {}).get("title", "")).lower()
    subtitle = (product.get("subtitle") or product.get("product_desc", {}).get("subtitle", "")).lower()
    combined = f"{title} {subtitle}"
    
    foreign_terms = [
        "auf deutsch", "german edition", "ausgabe", "tementa", "tormenta", 
        "spanish edition", "edicion", "french edition", "edition française", 
        "japanese edition", "italian edition", "portuguese edition", 
        "unabridged auf deutsch", "dutch edition", "russian edition"
    ]
    
    for term in foreign_terms:
        if term in combined:
            return True
            
    return False

def fetch_data():
    client = get_audible_client()
    print("Fetching your Audible library and analyzing series metadata...")

    library = client.get(
        "1.0/library",
        num_results=500,
        response_groups="product_desc,series,media,percent_complete,listening_status,product_attrs"
    )
    items = library.get("items", [])

    series_tracker = {}

    for item in items:
        if is_foreign_language(item):
            continue

        asin = item.get("asin")
        title = item.get("title") or item.get("product_desc", {}).get("title", "")
        cover_url = extract_cover(item)
        
        is_finished = item.get("is_finished", False)
        percent = item.get("percent_complete", 0)
        
        if is_finished or percent >= 98:
            status = "Completed"
        elif percent > 0:
            status = f"In Progress ({percent}%)"
        else:
            status = "Unread"

        series_list = item.get("series", [])
        if not series_list and "product_desc" in item:
            series_list = item.get("product_desc", {}).get("series", [])

        if not series_list:
            continue

        desc_title = item.get("product_desc", {}).get("title", "")
        omnibus_seqs = parse_omnibus_ranges(title).union(parse_omnibus_ranges(desc_title))

        for s in series_list:
            s_id = s.get("id") or s.get("asin")
            s_title = s.get("title") or s.get("name")
            sequence = s.get("sequence", "1")

            if not s_id or not s_title:
                continue

            s_title_clean = s_title.strip()

            if s_title_clean not in series_tracker:
                series_tracker[s_title_clean] = {
                    "title": s_title_clean,
                    "owned_asins": set(),
                    "owned_sequences": set(),
                    "owned_books": []
                }

            series_tracker[s_title_clean]["owned_asins"].add(asin)
            
            try:
                seq_num = int(float(sequence))
                series_tracker[s_title_clean]["owned_sequences"].add(seq_num)
            except ValueError:
                series_tracker[s_title_clean]["owned_sequences"].add(str(sequence))
                
            for o_seq in omnibus_seqs:
                series_tracker[s_title_clean]["owned_sequences"].add(o_seq)

            series_tracker[s_title_clean]["owned_books"].append({
                "sequence": sequence,
                "title": title,
                "cover": cover_url,
                "status": status,
                "asin": asin
            })

    print(f"Found {len(series_tracker)} series. Searching Audible catalog for missing volumes...")
    processed_series = []

    def safe_sort_key(x):
        val = str(x["sequence"])
        try:
            return (0, float(val))
        except ValueError:
            return (1, val)

    for s_title, s_info in series_tracker.items():
        for b in s_info["owned_books"]:
            box_seqs = parse_omnibus_ranges(b["title"])
            for bs in box_seqs:
                s_info["owned_sequences"].add(bs)

        s_info["owned_books"].sort(key=safe_sort_key)

        max_owned_seq = 0
        for seq_val in s_info["owned_sequences"]:
            try:
                s_num = float(seq_val)
                if s_num > max_owned_seq:
                    max_owned_seq = s_num
            except ValueError:
                pass

        max_completed_seq = -1
        for b in s_info["owned_books"]:
            if b["status"] == "Completed":
                box_seqs = parse_omnibus_ranges(b["title"])
                if box_seqs:
                    max_box = max(box_seqs)
                    if max_box > max_completed_seq:
                        max_completed_seq = max_box
                else:
                    try:
                        s_num = float(b["sequence"])
                        if s_num > max_completed_seq:
                            max_completed_seq = s_num
                    except ValueError:
                        pass

        if max_completed_seq > max_owned_seq:
            max_owned_seq = max_completed_seq

        if max_completed_seq > -1:
            for b in s_info["owned_books"]:
                box_seqs = parse_omnibus_ranges(b["title"])
                if box_seqs:
                    if min(box_seqs) <= max_completed_seq and b["status"] != "Completed":
                        b["status"] = "Completed (Assumed)"
                else:
                    try:
                        s_num = float(b["sequence"])
                        if s_num < max_completed_seq and b["status"] != "Completed":
                            b["status"] = "Completed (Assumed)"
                    except ValueError:
                        pass

        catalog_books = []
        try:
            search_results = client.get(
                "1.0/catalog/products",
                keywords=s_title,
                num_results=50,
                response_groups="product_desc,series,product_attrs"
            )
            
            products = search_results.get("products", [])
            for p in products:
                if is_foreign_language(p):
                    continue

                p_asin = p.get("asin")
                p_title = p.get("title") or p.get("product_desc", {}).get("title", "")
                
                p_series_list = p.get("series", [])
                if not p_series_list and "product_desc" in p:
                    p_series_list = p.get("product_desc", {}).get("series", [])
                
                belongs_to_series = False
                p_seq = "?"
                for ps in p_series_list:
                    ps_title = ps.get("title") or ps.get("name", "")
                    if s_title.lower() in ps_title.lower() or ps_title.lower() in s_title.lower():
                        belongs_to_series = True
                        p_seq = ps.get("sequence", "?")
                        break
                
                if belongs_to_series and p_asin:
                    p_cover = extract_cover(p) or get_fallback_cover(p_title)
                    catalog_books.append({
                        "asin": p_asin,
                        "title": p_title,
                        "sequence": p_seq,
                        "cover": p_cover
                    })
        except Exception as e:
            print(f"Catalog search note for '{s_title}': {e}")

        missing_books = []
        owned_asins = s_info["owned_asins"]
        owned_sequences = s_info["owned_sequences"]

        seen_catalog_asins = set()
        for cb in catalog_books:
            if cb["asin"] not in seen_catalog_asins:
                seen_catalog_asins.add(cb["asin"])
                
                if cb["asin"] in owned_asins:
                    continue
                
                cb_seq_str = str(cb["sequence"])
                
                box_set_ranges = parse_omnibus_ranges(cb["title"])
                if box_set_ranges:
                    all_covered = all(
                        seq <= max_owned_seq or seq in owned_sequences or str(seq) in owned_sequences 
                        for seq in box_set_ranges
                    )
                    if all_covered:
                        continue
                
                try:
                    cb_seq_num = float(cb_seq_str)
                    if cb_seq_num <= max_owned_seq:
                        continue
                except ValueError:
                    pass
                
                is_owned_by_sequence = False
                if cb_seq_str in owned_sequences:
                    is_owned_by_sequence = True
                else:
                    try:
                        cb_num = float(cb_seq_str)
                        if cb_num in owned_sequences or cb_num <= max_owned_seq:
                            is_owned_by_sequence = True
                    except ValueError:
                        pass
                
                if not is_owned_by_sequence:
                    missing_books.append(cb)

        s_info["owned_books"].sort(key=safe_sort_key)
        missing_books.sort(key=safe_sort_key)

        is_series_complete = (len(missing_books) == 0)

        processed_series.append({
            "title": s_info["title"],
            "owned": s_info["owned_books"],
            "missing": missing_books,
            "is_complete": is_series_complete
        })

    processed_series.sort(key=lambda x: x["title"])
    return processed_series

def generate_html_report(series_data):
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Audible Series Dashboard</title>
    <style>
        :root {{
            --bg-dark: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --accent-blue: #3b82f6;
            --green-badge: #10b981;
            --red-badge: #ef4444;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
        }}
        body {{
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 40px;
        }}
        .header {{
            max-width: 1400px;
            margin: 0 auto 20px auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .controls-bar {{
            max-width: 1400px;
            margin: 0 auto 20px auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--card-bg);
            padding: 12px 20px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}
        .ctrl-group {{
            display: flex;
            gap: 10px;
            align-items: center;
        }}
        .ctrl-btn {{
            background: #334155;
            color: #f8fafc;
            border: 1px solid #475569;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 0.85rem;
            cursor: pointer;
            font-weight: 600;
        }}
        .ctrl-btn:hover {{
            background: #475569;
        }}
        .toggle-label {{
            font-size: 0.9rem;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            user-select: none;
        }}
        .toggle-label input {{
            cursor: pointer;
            width: 16px;
            height: 16px;
        }}

        .accordion-container {{
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .series-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            transition: border-color 0.2s;
        }}
        .series-card:hover {{
            border-color: #475569;
        }}
        .series-card.hidden-completed {{
            display: none !important;
        }}
        .series-header {{
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            user-select: none;
            background: #1e293b;
        }}
        .series-title-area {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .arrow {{
            transition: transform 0.2s ease;
            font-size: 0.9rem;
            color: var(--text-sub);
        }}
        .series-card.active .arrow {{
            transform: rotate(90deg);
        }}
        .series-title {{
            font-size: 1.2rem;
            font-weight: 700;
            margin: 0;
            color: var(--text-main);
        }}
        .badge-area {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .badge {{
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        .badge-completed {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #10b981; }}
        .badge-missing {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #ef4444; }}
        
        .action-btn-group {{
            display: flex;
            gap: 8px;
            align-items: center;
        }}
        .override-btn, .search-audible-btn {{
            background: #334155;
            color: #f8fafc;
            border: 1px solid #475569;
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 0.78rem;
            cursor: pointer;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: background 0.2s, border-color 0.2s;
        }}
        .override-btn:hover, .search-audible-btn:hover {{
            background: #475569;
        }}
        .series-card.forced-complete .override-btn {{
            background: #064e3b;
            color: #a7f3d0;
            border-color: #10b981;
        }}

        .series-content {{
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease-out;
            background: #111827;
            padding: 0 24px;
        }}
        .series-card.active .series-content {{
            max-height: 2000px;
            padding: 24px;
            border-top: 1px solid var(--border-color);
        }}

        .grid-container {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
        }}
        @media (max-width: 1024px) {{
            .grid-container {{ grid-template-columns: 1fr; }}
        }}
        .column-title {{
            font-size: 0.95rem;
            color: var(--text-sub);
            margin-bottom: 15px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .book-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
        }}
        .book-card {{
            width: 120px;
            background: #1e293b;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
        }}
        .book-cover {{
            width: 120px;
            height: 120px;
            object-fit: cover;
            background: #334155;
        }}
        .book-details {{
            padding: 8px;
            font-size: 0.75rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            flex-grow: 1;
        }}
        .book-seq {{ font-weight: 700; color: var(--accent-blue); margin-bottom: 4px; }}
        .book-name {{
            font-weight: 500;
            line-height: 1.2;
            margin-bottom: 8px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .status-tag {{
            font-size: 0.7rem;
            padding: 3px 6px;
            border-radius: 4px;
            text-align: center;
            font-weight: 600;
            cursor: pointer;
            user-select: none;
        }}
        .status-tag:hover {{
            opacity: 0.8;
        }}
        .status-completed {{ background: #064e3b; color: #a7f3d0; }}
        .status-progress {{ background: #1e3a8a; color: #bfdbfe; }}
        .status-unread {{ background: #374151; color: #d1d5db; }}
        .buy-btn {{
            background: #f59e0b;
            color: #000;
            text-decoration: none;
            text-align: center;
            padding: 4px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 0.7rem;
            margin-top: 4px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📚 My Audible Series Dashboard</h1>
    </div>

    <div class="controls-bar">
        <div class="ctrl-group">
            <button class="ctrl-btn" onclick="expandAll()">Expand All</button>
            <button class="ctrl-btn" onclick="collapseAll()">Collapse All</button>
        </div>
        <div class="ctrl-group">
            <label class="toggle-label">
                <input type="checkbox" id="hideCompletedToggle" onchange="toggleHideCompleted(this)"> Hide Completed Series
            </label>
        </div>
    </div>

    <div class="accordion-container" id="accordionContainer">
"""

    for idx, s in enumerate(series_data):
        series_id = f"series_{idx}"
        missing_count = len(s["missing"])
        initial_is_complete = s["is_complete"]
        
        default_badge = '<span class="badge badge-completed">✓ Up to Date / Completed</span>' if initial_is_complete else f'<span class="badge badge-missing">{missing_count} Missing Volume(s)</span>'
        missing_display_style = "display: none;" if initial_is_complete else "display: block;"
        completed_class = "completed-series" if initial_is_complete else ""
        btn_display = "display: none;" if initial_is_complete else "display: inline-block;"
        
        encoded_series_title = urllib.parse.quote(s['title'])
        search_audible_url = f"https://www.audible.com/search?keywords={encoded_series_title}"
        
        html_content += f"""
        <div class="series-card {completed_class}" id="{series_id}" data-original-complete="{str(initial_is_complete).lower()}" data-missing-count="{missing_count}">
            <div class="series-header" onclick="toggleCard(event, this)">
                <div class="series-title-area">
                    <span class="arrow">▶</span>
                    <h2 class="series-title">{s['title']}</h2>
                </div>
                <div class="badge-area">
                    <div class="action-btn-group">
                        <a href="{search_audible_url}" target="_blank" class="search-audible-btn" onclick="event.stopPropagation()">Search on Audible</a>
                        <button class="override-btn" id="btn_{series_id}" style="{btn_display}" onclick="toggleSeriesOverride(event, '{series_id}')">Toggle Complete</button>
                    </div>
                    <div class="badge-wrapper">{default_badge}</div>
                </div>
            </div>
            <div class="series-content">
                <div class="grid-container">
                    <div>
                        <div class="column-title">Owned Books ({len(s['owned'])})</div>
                        <div class="book-list">
        """
        
        for b_idx, b in enumerate(s["owned"]):
            book_id = f"{series_id}_book_{b_idx}"
            st_class = "status-completed" if "Completed" in b["status"] else ("status-progress" if "Progress" in b["status"] else "status-unread")
            html_content += f"""
                            <div class="book-card">
                                <img class="book-cover" src="{b['cover']}" alt="Cover">
                                <div class="book-details">
                                    <div>
                                        <div class="book-seq">Book {b['sequence']}</div>
                                        <div class="book-name" title="{b['title']}">{b['title']}</div>
                                    </div>
                                    <div class="status-tag {st_class}" id="{book_id}" data-status="{b['status']}" onclick="cycleStatus(event, '{book_id}')">{b['status']}</div>
                                </div>
                            </div>
            """

        html_content += """
                        </div>
                    </div>
                    <div class="missing-column" style=\"""" + missing_display_style + """\">
        """

        if s["missing"]:
            html_content += f'<div class="column-title" style="color: #f87171;">Available to Purchase ({missing_count})</div><div class="book-list">'
            for m in s["missing"]:
                buy_link = f"https://www.audible.com/pd/{m['asin']}"
                html_content += f"""
                            <div class="book-card" style="border-style: dashed;">
                                <img class="book-cover" src="{m['cover']}" alt="Cover">
                                <div class="book-details">
                                    <div>
                                        <div class="book-seq" style="color:#ef4444;">Book {m['sequence']}</div>
                                        <div class="book-name" title="{m['title']}">{m['title']}</div>
                                    </div>
                                    <a href="{buy_link}" target="_blank" class="buy-btn">View on Audible</a>
                                </div>
                            </div>
                """
            html_content += '</div>'
        else:
            html_content += """
                        <div class="column-title">Available to Purchase</div>
                        <p style="color: #64748b; font-size: 0.9rem; margin-top: 10px;">No unowned volumes found in the Audible catalog for this series.</p>
            """

        html_content += """
                    </div>
                </div>
            </div>
        </div>
        """

    html_content += """
    </div>

    <script>
        document.addEventListener("DOMContentLoaded", () => {
            // Restore book statuses
            const savedStatuses = JSON.parse(localStorage.getItem("audible_book_statuses") || "{}");
            for (const [bookId, statusData] of Object.entries(savedStatuses)) {
                const el = document.getElementById(bookId);
                if (el) {
                    el.innerText = statusData.text;
                    el.className = "status-tag " + statusData.className;
                    el.setAttribute("data-status", statusData.text);
                }
            }

            // Restore series overrides (only apply to series that aren't naturally complete)
            const savedOverrides = JSON.parse(localStorage.getItem("audible_series_overrides") || "{}");
            for (const [seriesId, isForcedComplete] of Object.entries(savedOverrides)) {
                const card = document.getElementById(seriesId);
                if (card && card.getAttribute("data-original-complete") === "false") {
                    applySeriesState(card, isForcedComplete);
                }
            }

            // Restore Hide Completed toggle state & apply filter across all completed cards
            const hideCompletedPref = localStorage.getItem("audible_hide_completed") === "true";
            const toggleCheckbox = document.getElementById("hideCompletedToggle");
            if (toggleCheckbox) {
                toggleCheckbox.checked = hideCompletedPref;
                applyHideCompletedFilter(hideCompletedPref);
            }
        });

        function toggleCard(event, header) {
            if (event.target.tagName === 'BUTTON' || event.target.tagName === 'A' || event.target.closest('button') || event.target.closest('a')) return;
            const card = header.parentElement;
            card.classList.toggle('active');
        }

        function expandAll() {
            document.querySelectorAll(".series-card").forEach(card => {
                if (!card.classList.contains("hidden-completed")) {
                    card.classList.add("active");
                }
            });
        }

        function collapseAll() {
            document.querySelectorAll(".series-card").forEach(card => {
                card.classList.remove("active");
            });
        }

        function toggleHideCompleted(checkbox) {
            const hide = checkbox.checked;
            localStorage.setItem("audible_hide_completed", hide);
            applyHideCompletedFilter(hide);
        }

        function applyHideCompletedFilter(hide) {
            document.querySelectorAll(".series-card").forEach(card => {
                const isNaturallyComplete = card.getAttribute("data-original-complete") === "true";
                const isForcedComplete = card.classList.contains("forced-complete");
                
                if (hide && (isNaturallyComplete || isForcedComplete)) {
                    card.classList.add("hidden-completed");
                    card.classList.remove("active");
                } else {
                    card.classList.remove("hidden-completed");
                }
            });
        }

        function cycleStatus(event, bookId) {
            event.stopPropagation();
            const el = document.getElementById(bookId);
            const currentText = el.getAttribute("data-status");
            
            let nextText, nextClass;
            if (currentText.includes("Completed")) {
                nextText = "In Progress";
                nextClass = "status-progress";
            } else if (currentText.includes("In Progress")) {
                nextText = "Unread";
                nextClass = "status-unread";
            } else {
                nextText = "Completed";
                nextClass = "status-completed";
            }

            el.innerText = nextText;
            el.className = "status-tag " + nextClass;
            el.setAttribute("data-status", nextText);

            const savedStatuses = JSON.parse(localStorage.getItem("audible_book_statuses") || "{}");
            savedStatuses[bookId] = { text: nextText, className: nextClass };
            localStorage.setItem("audible_book_statuses", JSON.stringify(savedStatuses));
        }

        function toggleSeriesOverride(event, seriesId) {
            event.stopPropagation();
            const card = document.getElementById(seriesId);
            const isCurrentlyComplete = card.classList.contains("forced-complete");
            const newState = !isCurrentlyComplete;

            applySeriesState(card, newState);

            const savedOverrides = JSON.parse(localStorage.getItem("audible_series_overrides") || "{}");
            savedOverrides[seriesId] = newState;
            localStorage.setItem("audible_series_overrides", JSON.stringify(savedOverrides));

            const hideCompletedPref = document.getElementById("hideCompletedToggle").checked;
            applyHideCompletedFilter(hideCompletedPref);
        }

        function applySeriesState(card, isComplete) {
            const badgeWrapper = card.querySelector(".badge-wrapper");
            const missingColumn = card.querySelector(".missing-column");
            const missingCount = parseInt(card.getAttribute("data-missing-count") || "0");
            const seriesId = card.id;
            const overrideBtn = document.getElementById("btn_" + seriesId);

            if (isComplete) {
                card.classList.add("completed-series");
                card.classList.add("forced-complete");
                badgeWrapper.innerHTML = '<span class="badge badge-completed">✓ Up to Date / Completed</span>';
                missingColumn.style.display = "none";
                if (overrideBtn) overrideBtn.style.display = "inline-block";
            } else {
                card.classList.remove("completed-series");
                card.classList.remove("forced-complete");
                if (missingCount > 0) {
                    badgeWrapper.innerHTML = `<span class="badge badge-missing">${missingCount} Missing Volume(s)</span>`;
                    missingColumn.style.display = "block";
                    if (overrideBtn) overrideBtn.style.display = "inline-block";
                } else {
                    badgeWrapper.innerHTML = '<span class="badge badge-completed">✓ Up to Date / Completed</span>';
                    missingColumn.style.display = "none";
                    if (overrideBtn) overrideBtn.style.display = "none";
                }
            }
            
            const hideCompletedPref = document.getElementById("hideCompletedToggle").checked;
            applyHideCompletedFilter(hideCompletedPref);
        }
    </script>
</body>
</html>
"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\nDashboard successfully generated: {OUTPUT_HTML}")
    webbrowser.open(OUTPUT_HTML)

if __name__ == "__main__":
    data = fetch_data()
    generate_html_report(data)
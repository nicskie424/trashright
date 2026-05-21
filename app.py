from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "trashright-secret-2025")

# Admin password — change via environment variable ADMIN_PASSWORD
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

DB_PATH = os.path.join(BASE_DIR, "trashright.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # UNIQUE constraint on name prevents ALL duplicate rows
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS waste_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                instructions TEXT NOT NULL
            )
        """)
        # INSERT OR IGNORE = safe to call every startup, never re-seeds
        sample_data = [
            ("Plastic bottle",  "Recyclable",        "Rinse thoroughly and place in blue recycling bin. Remove cap and label if possible."),
            ("Banana peel",     "Biodegradable",      "Compost in green bin or backyard compost pile. Do not mix with other waste."),
            ("Battery",         "Hazardous",          "Take to designated battery recycling center. Do not throw in regular trash."),
            ("Paper",           "Recyclable",         "Flatten and place in blue recycling bin. Remove plastic windows from envelopes."),
            ("Food waste",      "Biodegradable",      "Scrape into green compost bin. Avoid meat, dairy, and oily foods in home compost."),
            ("Glass bottle",    "Recyclable",         "Rinse and place in glass recycling bin. Remove lid and label."),
            ("Aluminum can",    "Recyclable",         "Rinse and crush. Place in blue recycling bin."),
            ("Used oil",        "Hazardous",          "Take to oil recycling center or authorized collection point. Do not pour down drain."),
            ("Electronics",     "Hazardous",          "Take to e-waste recycling facility. Do not dispose in regular trash."),
            ("Cardboard",       "Recyclable",         "Flatten and bundle. Place in blue recycling bin."),
            ("Organic waste",   "Biodegradable",      "Compost in green bin. Includes vegetable peels, leaves, and grass clippings."),
            ("Styrofoam",       "Non-biodegradable",  "Check local facilities for polystyrene recycling. Otherwise, dispose in general waste."),
            ("Wood",            "Biodegradable",      "Compost untreated wood chips. Treated wood goes to special waste facility."),
            ("Metal scrap",     "Recyclable",         "Take to metal recycling center or place in designated metal bin."),
            ("Textiles",        "Recyclable",         "Donate usable clothes. Place non-usable textiles in textile recycling bin."),
            ("Pizza box",       "Recyclable",         "Remove food residue, tear off greasy parts, recycle clean cardboard."),
            ("Light bulb",      "Hazardous",          "Take to hazardous waste facility. Do not break or throw in trash."),
            ("Coffee grounds",  "Biodegradable",      "Add to compost pile. Great for garden soil amendment."),
        ]
        cursor.executemany(
            "INSERT OR IGNORE INTO waste_items (name, category, instructions) VALUES (?, ?, ?)",
            sample_data,
        )
        conn.commit()






@app.route("/api/autocomplete")
def autocomplete():
    raw = request.args.get("q", "").strip()
    if not raw or len(raw) < 2:
        return jsonify({"suggestions": []})
    if len(raw) > 80:
        return jsonify({"suggestions": []})

    query   = raw.lower()
    escaped = _like_escape(query)
    seen    = set()
    results = []

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1) Starts-with — highest priority
        cursor.execute(
            "SELECT name, category, icon FROM waste_items"
            " WHERE LOWER(name) LIKE ? ESCAPE '\\' ORDER BY name LIMIT 6",
            (f"{escaped}%",)
        )
        for row in cursor.fetchall():
            if row["name"].lower() not in seen:
                seen.add(row["name"].lower())
                results.append({
                    "name":     row["name"],
                    "category": row["category"],
                    "icon":     row["icon"] or "🗑️"
                })

        # 2) Contains — fill remaining slots
        if len(results) < 6:
            cursor.execute(
                "SELECT name, category, icon FROM waste_items"
                " WHERE LOWER(name) LIKE ? ESCAPE '\\'"
                " AND LOWER(name) NOT LIKE ? ESCAPE '\\'"
                " ORDER BY name LIMIT ?",
                (f"%{escaped}%", f"{escaped}%", 6 - len(results))
            )
            for row in cursor.fetchall():
                if row["name"].lower() not in seen:
                    seen.add(row["name"].lower())
                    results.append({
                        "name":     row["name"],
                        "category": row["category"],
                        "icon":     row["icon"] or "🗑️"
                    })

    return jsonify({"suggestions": results})

@app.route("/sw.js")
def service_worker():
    """Serve service worker from root scope — required for PWA."""
    response = app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Content-Type"]  = "application/javascript"
    return response


@app.route("/manifest.json")
def manifest():
    """Serve PWA manifest."""
    return app.send_static_file("manifest.json")


@app.route("/api/search")
def search_waste():
    raw_query = request.args.get("q", "")
    query = raw_query.lower().strip()

    if not query:
        return jsonify({"error": "Please enter a waste item.", "found": False}), 400
    if len(query) > 80:
        return jsonify({"error": "Search is too long.", "found": False}), 400

    query = re.sub(r"\s+", " ", query)
    escaped = _like_escape(query)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1) Exact match
        cursor.execute(
            "SELECT name, category, instructions, tips, icon FROM waste_items WHERE LOWER(name) = ? LIMIT 1",
            (query,),
        )
        exact = cursor.fetchone()

        # 2) Other contains-matches (different items only)
        cursor.execute(
            """
            SELECT name, category, instructions, tips, icon FROM waste_items
            WHERE LOWER(name) LIKE ? ESCAPE '\\'
              AND LOWER(name) != ?
            ORDER BY
                CASE WHEN LOWER(name) LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END,
                name
            LIMIT 4
            """,
            (f"%{escaped}%", query, f"{escaped}%"),
        )
        related = cursor.fetchall()

        # 3) No results at all — try word-by-word partial
        if not exact and not related:
            for word in query.split():
                if len(word) < 3:
                    continue
                w = _like_escape(word)
                cursor.execute(
                    "SELECT name, category, instructions, tips, icon FROM waste_items WHERE LOWER(name) LIKE ? ESCAPE '\\' LIMIT 1",
                    (f"%{w}%",),
                )
                row = cursor.fetchone()
                if row:
                    exact = row
                    break

        if not exact and not related:
            return jsonify({"error": "Item not found. Try another term or check spelling.", "found": False}), 404

        matches = []
        seen = set()

        if exact:
            matches.append({"name": exact["name"], "category": exact["category"], "instructions": exact["instructions"], "tips": exact["tips"] if exact["tips"] else "", "icon": exact["icon"] if exact["icon"] else "🗑️"})
            seen.add(exact["name"].lower())

        for row in related:
            if row["name"].lower() not in seen:
                seen.add(row["name"].lower())
                matches.append({"name": row["name"], "category": row["category"], "instructions": row["instructions"], "tips": row["tips"] if row["tips"] else "", "icon": row["icon"] if row["icon"] else "🗑️"})

        # 4) Pad with same-category items if fewer than 2 matches
        if len(matches) < 2 and matches:
            primary_cat = matches[0]["category"]
            placeholders = ",".join("?" * len(seen))
            cursor.execute(
                f"""
                SELECT name, category, instructions, tips, icon FROM waste_items
                WHERE category = ? AND LOWER(name) NOT IN ({placeholders})
                ORDER BY RANDOM()
                LIMIT ?
                """,
                [primary_cat] + list(seen) + [5 - len(matches)],
            )
            for row in cursor.fetchall():
                if row["name"].lower() not in seen:
                    seen.add(row["name"].lower())
                    matches.append({"name": row["name"], "category": row["category"], "instructions": row["instructions"], "tips": row["tips"] if row["tips"] else "", "icon": row["icon"] if row["icon"] else "🗑️"})

    return jsonify({"found": True, "matches": matches[:5], "query": raw_query})


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            return redirect(url_for("results", q=query))
    return render_template("index.html")


@app.route("/results")
def results():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("index"))
    return render_template("results.html", query=query)


@app.route("/category/<category_name>")
def category(category_name):
    return render_template("category.html", category=category_name)


@app.route("/api/category/<category_name>")
def get_category_items(category_name):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, category, instructions, tips, icon FROM waste_items WHERE category = ? ORDER BY name",
            (category_name,),
        )
        items = [{"name": r["name"], "category": r["category"], "instructions": r["instructions"], "tips": r["tips"] if r["tips"] else "", "icon": r["icon"] if r["icon"] else "🗑️"} for r in cursor.fetchall()]
    if not items:
        return jsonify({"error": "Category not found.", "found": False}), 404
    return jsonify({"found": True, "category": category_name, "items": items})


@app.route("/api/all_categories")
def get_categories():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category, COUNT(*) as count FROM waste_items GROUP BY category ORDER BY category"
        )
        categories = [{"name": row["category"], "count": row["count"]} for row in cursor.fetchall()]
    return jsonify({"categories": categories})





@app.route("/api/cache-version")
def cache_version():
    """Returns a version hash based on item count + last modified.
    Service worker polls this to detect when admin adds/edits/deletes items."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM waste_items")
        count = cursor.fetchone()["cnt"]
    return jsonify({"version": f"v{count}", "count": count})

# ─────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────

def admin_logged_in():
    return session.get("admin_authenticated") is True


@app.route("/admin", methods=["GET", "POST"])
def admin():
    error = None

    # Handle login form
    if request.method == "POST" and "password" in request.form:
        if request.form["password"] == ADMIN_PASSWORD:
            session["admin_authenticated"] = True
            return redirect(url_for("admin"))
        else:
            error = "Incorrect password. Please try again."

    # Not logged in — show login page
    if not admin_logged_in():
        return render_template("admin.html", view="login", error=error)

    # Handle add item
    if request.method == "POST" and "item_name" in request.form:
        name         = request.form.get("item_name",     "").strip()
        category     = request.form.get("category",      "").strip()
        icon         = request.form.get("icon",          "🗑️").strip()
        instructions = request.form.get("instructions",  "").strip()
        tips         = request.form.get("tips",          "").strip()

        if not name or not category or not instructions:
            flash("error:Name, category, and instructions are required.")
        else:
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        "INSERT INTO waste_items (name, category, icon, instructions, tips) VALUES (?,?,?,?,?)",
                        (name, category, icon, instructions, tips)
                    )
                    conn.commit()
                flash(f"success:{name} added successfully!")
            except Exception as e:
                if "UNIQUE" in str(e):
                    flash(f"error:An item named '{name}' already exists.")
                else:
                    flash(f"error:Error adding item: {str(e)}")
        return redirect(url_for("admin"))

    # Handle delete
    if request.method == "POST" and "delete_id" in request.form:
        item_id = request.form.get("delete_id")
        with get_db_connection() as conn:
            conn.execute("DELETE FROM waste_items WHERE id = ?", (item_id,))
            conn.commit()
        flash("success:Item deleted.")
        return redirect(url_for("admin"))

    # Load all items for display
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, category, icon, instructions, tips FROM waste_items ORDER BY category, name"
        )
        items = [dict(r) for r in cursor.fetchall()]

    categories = ["Recyclable", "Biodegradable", "Hazardous", "Non-biodegradable"]
    return render_template("admin.html", view="dashboard", items=items, categories=categories)


@app.route("/admin/edit/<int:item_id>", methods=["GET", "POST"])
def admin_edit(item_id):
    if not admin_logged_in():
        return redirect(url_for("admin"))

    if request.method == "POST":
        name         = request.form.get("item_name",    "").strip()
        category     = request.form.get("category",     "").strip()
        icon         = request.form.get("icon",         "🗑️").strip()
        instructions = request.form.get("instructions", "").strip()
        tips         = request.form.get("tips",         "").strip()

        if not name or not category or not instructions:
            flash("error:Name, category, and instructions are required.")
            return redirect(url_for("admin_edit", item_id=item_id))

        try:
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE waste_items SET name=?, category=?, icon=?, instructions=?, tips=? WHERE id=?",
                    (name, category, icon, instructions, tips, item_id)
                )
                conn.commit()
            flash(f"success:{name} updated successfully!")
            return redirect(url_for("admin"))
        except Exception as e:
            flash(f"error:Error updating: {str(e)}")
            return redirect(url_for("admin_edit", item_id=item_id))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM waste_items WHERE id = ?", (item_id,))
        item = cursor.fetchone()

    if not item:
        flash("error:Item not found.")
        return redirect(url_for("admin"))

    categories = ["Recyclable", "Biodegradable", "Hazardous", "Non-biodegradable"]
    return render_template("admin.html", view="edit", item=dict(item), categories=categories)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin"))


with app.app_context():
    init_db()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port  = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug)

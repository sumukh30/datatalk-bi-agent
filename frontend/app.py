from datatalk.sql_reranker import rerank_sql, _fix_date_format
from dotenv import load_dotenv
load_dotenv(dotenv_path="../.env")  # loads from datatalk-agent/.env

import os
import sys

# Add parent directory so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import vertexai
from schema_tool import get_schema_context
from datatalk.sql_reranker import rerank_sql
from firestore_store import save_query, get_recent_queries
from google.cloud import bigquery

app = Flask(__name__)
CORS(app)

PROJECT_ID = "datatalk-agent"
LOCATION = "us-central1"

vertexai.init(project=PROJECT_ID, location=LOCATION)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/schema", methods=["GET"])
def get_schema():
    """Returns the BigQuery schema for display in the UI."""
    try:
        schema = get_schema_context()
        return jsonify({"schema": schema})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-sql", methods=["POST"])
def generate_sql():
    """
    Step 1 of the flow: takes a question, returns the best SQL.
    Does NOT execute — waits for user confirmation.
    """
    data = request.json
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "Question is required"}), 400

    try:
        schema = get_schema_context()
        result = rerank_sql(question, schema)

        return jsonify({
            "question": question,
            "best_sql": result["best_sql"],
            "confidence_score": result["confidence_score"],
            "preview_rows": result["preview_rows"],
            "row_count": result["row_count"],
            "all_candidates": len(result["all_candidates"]),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/execute-sql", methods=["POST"])
def execute_sql():
    """
    Step 2 of the flow: executes the approved SQL after user confirmation.
    Only called after the user clicks confirm.
    """
    data = request.json
    sql = data.get("sql", "").strip()
    question = data.get("question", "").strip()

    if not sql:
        return jsonify({"error": "SQL is required"}), 400

    sql = _fix_date_format(sql)
    try:
        client = bigquery.Client(project=PROJECT_ID)
        query_job = client.query(sql)
        rows = list(query_job.result())
        results = [dict(row) for row in rows]

        # Build a simple result summary for Firestore
        result_summary = f"{len(results)} rows returned"
        try:
            if len(results) == 1 and len(results[0]) == 1:
                val = list(results[0].values())[0]
                result_summary = f"Result: {val}"
        except (IndexError, StopIteration):
            pass

        # Save to Firestore history
        save_query(question, sql, result_summary)

        # Generate one-line business insight
        insight = ""
        try:
            from vertexai.generative_models import GenerativeModel
            insight_model = GenerativeModel("gemini-2.5-flash")
            insight_prompt = f"In exactly one sentence, give a sharp business insight from this data. Question was: '{question}'. Data: {results[:5]}"
            insight = insight_model.generate_content(insight_prompt).text.strip()
        except Exception:
            insight = ""

        return jsonify({
            "results": results,
            "row_count": len(results),
            "result_summary": result_summary,
            "insight": insight,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/generate-report", methods=["POST"])
def generate_report():
    """Generates a 3-paragraph business report from query results."""
    try:
        from vertexai.generative_models import GenerativeModel

        data = request.json
        question = data.get("question", "")
        results = data.get("results", [])
        sql = data.get("sql", "")

        model = GenerativeModel("gemini-2.5-flash")
        prompt = f"""You are a business intelligence analyst. 
Write a professional 3-paragraph report based on the following data query and results.

Question asked: {question}
SQL executed: {sql}
Data returned: {results[:20]}

Paragraph 1: Summarize the key finding in plain English for a non-technical business operator.
Paragraph 2: Highlight 2-3 specific numbers or patterns that stand out from the data.
Paragraph 3: Give 1-2 actionable business recommendations based on this data.

Write in a professional but accessible tone. No markdown headers, just paragraphs."""

        response = model.generate_content(prompt)
        report = response.text.strip()

        return jsonify({"report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/fivetran-status", methods=["GET"])
def fivetran_status():
    """Returns Fivetran connector status for display in UI."""
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        import os

        api_key = os.getenv("FIVETRAN_API_KEY")
        api_secret = os.getenv("FIVETRAN_API_SECRET")

        resp = requests.get(
            "https://api.fivetran.com/v1/connectors",
            auth=HTTPBasicAuth(api_key, api_secret),
            timeout=5
        )
        data = resp.json()
        connectors = []
        for item in data.get("data", {}).get("items", []):
            connectors.append({
                "name": item.get("schema", "unknown"),
                "status": item.get("status", {}).get("sync_state", "unknown"),
                "last_sync": item.get("succeeded_at", "Never"),
            })
        return jsonify({"connectors": connectors})
    except Exception as e:
        return jsonify({"connectors": [], "error": str(e)})

@app.route("/api/history", methods=["GET"])
def get_history():
    """Returns recent query history from Firestore."""
    try:
        history = get_recent_queries(limit=5)
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/trigger-sync", methods=["POST"])
def trigger_sync():
    """Triggers a manual sync on all Fivetran connectors."""
    try:
        import requests
        from requests.auth import HTTPBasicAuth

        api_key = os.getenv("FIVETRAN_API_KEY")
        api_secret = os.getenv("FIVETRAN_API_SECRET")
        auth = HTTPBasicAuth(api_key, api_secret)

        # First get all connectors
        resp = requests.get(
            "https://api.fivetran.com/v1/connectors",
            auth=auth,
            timeout=5
        )
        data = resp.json()
        connectors = [
            item for item in data.get("data", {}).get("items", [])
            if "datatalk" in item.get("schema", "")
        ]

        triggered = []
        errors = []

        for connector in connectors:
            connector_id = connector.get("id")
            schema = connector.get("schema")

            sync_resp = requests.post(
                f"https://api.fivetran.com/v1/connectors/{connector_id}/sync",
                auth=auth,
                json={"force": True},
                timeout=5
            )

            if sync_resp.status_code == 200:
                triggered.append(schema)
            else:
                errors.append({
                    "schema": schema,
                    "error": sync_resp.text
                })

        return jsonify({
            "triggered": triggered,
            "errors": errors,
            "message": f"Sync triggered for: {', '.join(triggered)}" if triggered else "No connectors triggered"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sync-status", methods=["GET"])
def sync_status():
    """Polls current sync state of all datatalk connectors."""
    try:
        import requests
        from requests.auth import HTTPBasicAuth

        api_key = os.getenv("FIVETRAN_API_KEY")
        api_secret = os.getenv("FIVETRAN_API_SECRET")

        resp = requests.get(
            "https://api.fivetran.com/v1/connectors",
            auth=HTTPBasicAuth(api_key, api_secret),
            timeout=5
        )
        data = resp.json()

        statuses = []
        for item in data.get("data", {}).get("items", []):
            if "datatalk" in item.get("schema", ""):
                statuses.append({
                    "schema": item.get("schema"),
                    "sync_state": item.get("status", {}).get("sync_state", "unknown"),
                    "succeeded_at": item.get("succeeded_at", "Never"),
                })

        return jsonify({"statuses": statuses})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/pipeline-alert", methods=["GET"])
def pipeline_alert():
    """Checks if any connector has sync issues."""
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        api_key = os.getenv("FIVETRAN_API_KEY")
        api_secret = os.getenv("FIVETRAN_API_SECRET")
        resp = requests.get(
            "https://api.fivetran.com/v1/connectors",
            auth=HTTPBasicAuth(api_key, api_secret),
            timeout=5
        )
        data = resp.json()
        alerts = []
        for item in data.get("data", {}).get("items", []):
            state = item.get("status", {}).get("sync_state", "")
            if state not in ["scheduled", "syncing"]:
                alerts.append({
                    "connector": item.get("schema"),
                    "state": state,
                    "last_sync": item.get("succeeded_at", "Never")
                })
        return jsonify({"alerts": alerts, "healthy": len(alerts) == 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
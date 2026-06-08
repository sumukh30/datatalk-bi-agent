import re
import vertexai
from vertexai.generative_models import GenerativeModel
from google.cloud import bigquery
import os

PROJECT_ID = "datatalk-agent"
DATASET_ID = "datatalk"
LOCATION = "us-central1"

vertexai.init(project=PROJECT_ID, location=LOCATION)


def _fix_date_format(sql):
    """Replace PARSE_DATE with wrong %Y-%m-%d with correct %m/%d/%Y."""
    return re.sub(r"PARSE_DATE\s*\(\s*['\"]%Y-%m-%d['\"]\s*,", "PARSE_DATE('%m/%d/%Y',", sql)


def generate_sql_candidates(question, schema, n_candidates=5):
    temperatures = [0.0, 0.2, 0.5, 0.7, 1.0][:n_candidates]
    candidates = []

    prompt = """You are a BigQuery SQL expert. Write a single valid SQL query to answer the user's question.

CRITICAL DATE RULE: order_date and signup_date are STRING columns containing values like '4/13/2026' and '1/5/2026'.
The ONLY correct way to parse them is: PARSE_DATE('%m/%d/%Y', order_date)
NEVER use PARSE_DATE('%Y-%m-%d', ...) it will always fail with this data.

EXAMPLE of correct date SQL:
Question: How many orders per month in 2026?
Answer: SELECT FORMAT_DATE('%Y-%m', PARSE_DATE('%m/%d/%Y', order_date)) AS month, COUNT(order_id) AS total_orders FROM `datatalk-agent.datatalk.orders` WHERE EXTRACT(YEAR FROM PARSE_DATE('%m/%d/%Y', order_date)) = 2026 GROUP BY month ORDER BY month

EXAMPLE of correct top-N SQL:
Question: Which product category has the most orders?
Answer: SELECT product_category, COUNT(order_id) AS total_orders FROM `datatalk-agent.datatalk.orders` GROUP BY product_category ORDER BY total_orders DESC LIMIT 10

EXAMPLE of correct join SQL:
Question: Which city has the highest total order value?
Answer: SELECT c.city, SUM(o.amount) AS total_value FROM `datatalk-agent.datatalk.orders` o JOIN `datatalk-agent.datatalk.customers` c ON o.customer_id = c.customer_id GROUP BY c.city ORDER BY total_value DESC LIMIT 10

OTHER RULES:
- Use fully qualified table names: `datatalk-agent.datatalk.table_name`
- Return ONLY the SQL query, no explanation, no markdown, no backticks
- Use standard BigQuery SQL syntax
- For top/most/highest/best questions: SELECT the label AND the metric, ORDER BY metric DESC, LIMIT 10
- Ignore columns _row and _fivetran_synced

SCHEMA:
""" + schema + """

QUESTION: """ + question + """

SQL:"""

    for temp in temperatures:
        model = GenerativeModel(
            "gemini-2.5-flash",
            generation_config={"temperature": temp}
        )
        try:
            response = model.generate_content(prompt)
            sql = response.text.strip()
            sql = sql.replace("```sql", "").replace("```", "").strip()
            sql = _fix_date_format(sql)
            if sql.strip().upper().endswith("LIMIT 1"):
                sql = sql[:-1] + "10"
            if sql not in candidates:
                candidates.append(sql)
        except Exception as e:
            print(f"Candidate at temp {temp} failed: {e}")

    return candidates


def score_and_rank(candidates, question):
    client = bigquery.Client(project=PROJECT_ID)
    scored = []

    for sql in candidates:
        score = 0
        row_count = 0
        error = None
        preview = []

        try:
            preview_sql = f"SELECT * FROM ({sql}) LIMIT 100"
            job = client.query(preview_sql)
            rows = list(job.result())
            row_count = len(rows)
            preview = [dict(r) for r in rows[:3]]

            score += 3
            if row_count > 0:
                score += 2
            if row_count < 10000:
                score += 1

        except Exception as e:
            error = str(e)
            score = 0

        scored.append({
            "sql": sql,
            "score": score,
            "row_count": row_count,
            "preview": preview,
            "error": error,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    best_sql = scored[0]["sql"] if scored else candidates[0]
    return best_sql, scored


def rerank_sql(question, schema):
    print(f"\n Generating SQL candidates for: {question}")
    candidates = generate_sql_candidates(question, schema)
    print(f"Generated {len(candidates)} candidates")

    best_sql, ranked = score_and_rank(candidates, question)
    print(f"Best SQL (score {ranked[0]['score']}): {best_sql}")

    return {
        "best_sql": best_sql,
        "confidence_score": ranked[0]["score"],
        "preview_rows": ranked[0]["preview"],
        "row_count": ranked[0]["row_count"],
        "all_candidates": ranked,
    }


if __name__ == "__main__":
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from schema_tool import get_schema_context
    schema = get_schema_context()
    result = rerank_sql(
        question="How many orders were placed in total?",
        schema=schema
    )
    print(f"\nBest SQL: {result['best_sql']}")
    print(f"Confidence: {result['confidence_score']}/6")
    print(f"Preview: {result['preview_rows']}")
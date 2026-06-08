import os
import vertexai
from vertexai.generative_models import GenerativeModel
from google.cloud import bigquery
from schema_tool import get_schema_context

PROJECT_ID = "datatalk-agent"
DATASET_ID = "datatalk"
LOCATION = "us-central1"

# Initialize Vertex AI
vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel("gemini-2.5-flash")


def generate_sql(user_question: str) -> str:
    """
    Takes a plain English question, fetches real schema,
    and asks Gemini to write a BigQuery SQL query.
    """
    schema = get_schema_context()

    prompt = f"""You are a BigQuery SQL expert. Given the schema below,
write a single valid BigQuery SQL query to answer the user's question.

RULES:
- Use fully qualified table names: `{PROJECT_ID}.{DATASET_ID}.table_name`
- Return ONLY the SQL query, nothing else
- No markdown formatting, no backticks, no explanation
- Use standard BigQuery SQL syntax

SCHEMA:
{schema}

USER QUESTION: {user_question}

SQL QUERY:"""

    response = model.generate_content(prompt)
    return response.text.strip()


def execute_sql(sql: str) -> list:
    """Runs the SQL against BigQuery and returns results."""
    client = bigquery.Client(project=PROJECT_ID)
    query_job = client.query(sql)
    results = list(query_job.result())
    return [dict(row) for row in results]


if __name__ == "__main__":
    question = "How many orders were placed in total?"

    print(f"Question: {question}\n")

    sql = generate_sql(question)
    print(f"Generated SQL:\n{sql}\n")

    results = execute_sql(sql)
    print(f"Results: {results}")
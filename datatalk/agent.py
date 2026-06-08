import os
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"

import vertexai
import google.auth
from google.adk.agents import Agent
from google.adk.tools.bigquery import BigQueryCredentialsConfig, BigQueryToolset
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schema_tool import get_schema_context
from datatalk.sql_reranker import rerank_sql
from firestore_store import save_query, get_recent_queries, get_similar_past_query

vertexai.init(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_LOCATION")
)

# --- BigQueryToolset using ADC ---
application_default_credentials, _ = google.auth.default()
credentials_config = BigQueryCredentialsConfig(
    credentials=application_default_credentials
)
tool_config = BigQueryToolConfig(write_mode=WriteMode.BLOCKED)

bigquery_toolset = BigQueryToolset(
    credentials_config=credentials_config,
    bigquery_tool_config=tool_config,
)

# --- Fivetran MCP ---
fivetran_mcp = MCPToolset(
    connection_params=StdioServerParameters(
        command="uvx",
        args=["--from", "git+https://github.com/fivetran/fivetran-mcp", "fivetran-mcp"],
        env={
            "FIVETRAN_API_KEY": os.getenv("FIVETRAN_API_KEY"),
            "FIVETRAN_API_SECRET": os.getenv("FIVETRAN_API_SECRET"),
            "FIVETRAN_ALLOW_WRITES": "false",
        }
    )
)


# --- Custom function tools ---
def get_schema() -> str:
    """Fetches the real BigQuery schema for the datatalk dataset."""
    return get_schema_context()


def generate_best_sql(question: str) -> dict:
    """
    Generates multiple SQL candidates and returns the best one
    using execution-guided reranking. Always use this before
    running any SQL query.

    Args:
        question: The user's natural language question about their data.

    Returns:
        A dict with best_sql, confidence_score, and preview_rows.
    """
    schema = get_schema_context()
    return rerank_sql(question, schema)


def save_completed_query(question: str, sql: str, result_summary: str) -> str:
    """
    Saves a completed query and its result to history.

    Args:
        question: The original user question.
        sql: The SQL that was executed.
        result_summary: A brief summary of what the result showed.

    Returns:
        The saved document ID.
    """
    return save_query(question, sql, result_summary)


def get_query_history() -> list:
    """
    Returns the 5 most recent queries the user has asked.
    Use this to provide context about past questions.
    """
    return get_recent_queries(limit=5)


def find_similar_past_query(question: str) -> dict:
    """
    Looks for a similar question asked before and returns
    its SQL if found, so we can reuse it.

    Args:
        question: The current user question.
    """
    result = get_similar_past_query(question)
    if result:
        return result
    return {"message": "No similar past query found"}


# --- Root agent ---
root_agent = Agent(
    name="datatalk_agent",
    model="gemini-2.5-flash",
    description="A business intelligence agent that answers data questions in plain English.",
    instruction="""You are DataTalk, a business intelligence assistant for non-technical users.

        You have access to:
        1. Fivetran tools — to check pipeline and connector status
        2. BigQuery tools — to explore schemas and run SQL queries
        3. generate_best_sql — YOUR PRIMARY TOOL for answering data questions
        4. save_completed_query — call this after every successful query
        5. get_query_history — to see what the user has asked before
        6. find_similar_past_query — to reuse past SQL when relevant

        Dataset: datatalk-agent.datatalk
        Tables:
        - orders (order_id, customer_id, product_category, order_date STRING 'M/D/YYYY e.g. 4/13/2026', amount, status)
        - customers (customer_id, full_name, email, signup_date STRING 'M/D/YYYY e.g. 1/5/2026', city, total_orders)
        - Ignore columns: _row, _fivetran_synced

        WORKFLOW for every data question:
        Step 1: Call find_similar_past_query to check if this was asked before
        Step 2: Call generate_best_sql with the user's question
        Step 3: Show the user the best_sql and confidence_score
        Step 4: Say exactly: "Here's the SQL I plan to run — does it look right? Reply 'yes' to execute or tell me what to change."
        Step 5: WAIT for the user to reply before doing anything else
        Step 6: If confirmed with 'yes', call the BigQuery execute_sql tool with the approved SQL
        Step 7: Return results as a clear natural language answer with key numbers highlighted
        Step 8: Call save_completed_query with the question, SQL, and a one-sentence result summary

        IMPORTANT RULES:
        - NEVER execute SQL without explicit user confirmation — this is mandatory
        - If confidence_score is below 4, warn the user: "I'm not very confident about this query, please review carefully"
        - Always use generate_best_sql instead of writing SQL yourself
        - Be conversational and friendly — users are non-technical
        - order_date and signup_date are STRING type in format 'M/D/YYYY' e.g. '4/13/2026'. Always use PARSE_DATE('%m/%d/%Y', order_date) for date operations
        - For joins use: orders.customer_id = customers.customer_id
        """
    ,
    tools=[
        fivetran_mcp,
        bigquery_toolset,
        get_schema,
        generate_best_sql,
        save_completed_query,
        get_query_history,
        find_similar_past_query,
    ],
)
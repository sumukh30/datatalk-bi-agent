from google.cloud import firestore
from datetime import datetime, timezone
PROJECT_ID = "datatalk-agent"

# ADC handles auth automatically — no credentials needed
db = firestore.Client(project=PROJECT_ID, database="datatalk-db")

def save_query(question: str, sql: str, result_summary: str) -> str:
    """
    Saves a completed query to Firestore.
    Returns the document ID.
    """
    doc_ref = db.collection("queries").document()
    doc_ref.set({
        "question": question,
        "sql": sql,
        "result_summary": result_summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    print(f"Saved query with ID: {doc_ref.id}")
    return doc_ref.id


def get_recent_queries(limit: int = 5) -> list:
    """
    Retrieves the most recent queries from Firestore.
    Used to give the agent context about past questions.
    """
    queries_ref = db.collection("queries")
    docs = queries_ref.order_by(
        "timestamp",
        direction=firestore.Query.DESCENDING
    ).limit(limit).stream()

    results = []
    for doc in docs:
        results.append(doc.to_dict())
    return results


def get_similar_past_query(question: str) -> dict | None:
    """
    Simple keyword-based lookup for similar past queries.
    Returns the most recent matching query or None.
    """
    all_queries = get_recent_queries(limit=20)
    question_words = set(question.lower().split())

    best_match = None
    best_score = 0

    for q in all_queries:
        past_words = set(q.get("question", "").lower().split())
        overlap = len(question_words & past_words)
        if overlap > best_score:
            best_score = overlap
            best_match = q

    # Only return if there's meaningful overlap (3+ words)
    return best_match if best_score >= 3 else None


# Test when run directly
if __name__ == "__main__":
    # Save a test query
    save_query(
        question="How many orders were placed in total?",
        sql="SELECT COUNT(*) as total FROM `datatalk-agent.datatalk.orders`",
        result_summary="30 total orders"
    )

    # Read it back
    recent = get_recent_queries(limit=3)
    print(f"\nRecent queries: {recent}")
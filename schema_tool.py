from google.cloud import bigquery

PROJECT_ID = "datatalk-agent"
DATASET_ID = "datatalk"

def get_schema_context():
    """
    Fetches real schema from BigQuery and returns it
    as a formatted string ready to inject into a Gemini prompt.
    """
    client = bigquery.Client(project=PROJECT_ID)
    dataset_ref = client.dataset(DATASET_ID)
    tables = list(client.list_tables(dataset_ref))

    schema_lines = [f"Dataset: `{PROJECT_ID}.{DATASET_ID}`\n"]

    for table in tables:
        table_ref = client.get_table(
            f"{PROJECT_ID}.{DATASET_ID}.{table.table_id}"
        )
        schema_lines.append(f"Table: `{table.table_id}`")
        schema_lines.append("Columns:")
        for field in table_ref.schema:
            schema_lines.append(f"  - {field.name} ({field.field_type})")
        schema_lines.append("")

    return "\n".join(schema_lines)


if __name__ == "__main__":
    print(get_schema_context())
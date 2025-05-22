import openai
import psycopg2  # or use sqlite3
import sqlparse
import re
import orjson
import plotly.io as pio
import os
import logger
import plotly.graph_objects as go
import plotly.express as px
from dotenv import load_dotenv

load_dotenv(override=True)
openai.api_key = os.getenv("OPENAI_API_KEY")

print("Loaded OpenAI Key:", openai.api_key[-8:] + "..." if openai.api_key else "Not found!")

# === 1. Connect to database ===
def get_connection():
    return psycopg2.connect(
        dbname="raccoon",
        user="postgres",
        password="postgres",
        host="localhost",
        port=5433
    )

# === 2. Get schema with sample rows ===
def get_schema_with_samples(conn):
    cursor = conn.cursor()
    schema = ""

    cursor.execute("""SELECT table_name FROM information_schema.tables 
                      WHERE table_schema='public' AND table_type='BASE TABLE';""")
    tables = cursor.fetchall()

    descriptive_keywords = ['type', 'role', 'status', 'plate_number', 'name', 'first_name', 'last_name']

    for (table,) in tables:
        cursor.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}'")
        columns = cursor.fetchall()
        column_names = [col for col, _ in columns]

        schema += f"\nTable: {table}\nColumns:\n"
        schema += "\n".join([f"  - {col} ({dtype})" for col, dtype in columns])

        # Sample rows
        cursor.execute(f"SELECT * FROM {table} LIMIT 3")
        rows = cursor.fetchall()
        schema += f"\nSample rows:\n{rows}"

        # Add distinct values for descriptive columns
        for col in column_names:
            if col.lower() not in descriptive_keywords:
                continue
            cursor.execute(f'SELECT DISTINCT "{col}" FROM "{table}"')
            distinct_vals = cursor.fetchall()
            if distinct_vals:
                val_list = [str(val[0]) for val in distinct_vals if val[0] is not None]
                schema += f"\nDistinct values for {table}.{col}:\n  - " + ", ".join(val_list)

        schema += "\n\n"

    return schema.strip()


# === 3. Validate query (must be safe) ===
def is_safe_sql(query):
    parsed = sqlparse.parse(query)
    for statement in parsed:
        tokens = [token.value.lower() for token in statement.tokens if not token.is_whitespace]
        if not any(token.startswith("select") for token in tokens):
            return False
        if any(re.search(r"\b(password|phone|bank|email|delete|drop|update|insert|grant|revoke)\b", token) for token in tokens):
            return False
    return True

# === 4. Run query locally ===
def run_sql(query, conn, schema_context=None, user_question=None, retry=True):
    cursor = conn.cursor()
    try:
        cursor.execute(query)
    except Exception as e:
        error_message = str(e)
        print("Original SQL failed:", error_message)
        if retry and schema_context and user_question:
            corrected_query = retry_query_with_error_context(query, error_message, schema_context, user_question)
            print("Retrying with corrected SQL:", corrected_query)
            try:
                conn.rollback()
                cursor.execute(corrected_query)
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                return {"columns": columns, "rows": rows, "corrected": True}
            except Exception as retry_e:
                return {"error": f"Retry also failed: {str(retry_e)}", "original_error": error_message}
        return {"error": error_message}
    
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return {"columns": columns, "rows": rows}


# === 5. Ask GPT for query generation ===
def generate_sql(user_question, schema_context):
    messages = [
        {"role": "system", "content": f"""You are an assistant that generates SQL queries return only query itself without any comments. Use only SELECT and try to use only meaningful columns such as name, not just ids, use joins when necessary and do not confuse table name aliases. There is always table_name.id column in each table as primary key .You can order the results to return the most informative data in the database.
Never query for all columns from a table. You must query only the columns that are needed to answer the question. Wrap each column name in double quotes (") to denote them as delimited identifiers.
Pay attention to use only the column names you can see in the tables below. Be careful to not query for columns that do not exist in your selecting table. Also, pay attention to which column is in which table.
Pay attention to use CURRENT_DATE function to get the current date, if the question involves "today". Schema:\n{schema_context} pickup location table is one to one with locations table, if you want to get pickuplocation name, join with location and get locaiton.name instead."""},
        {"role": "user", "content": user_question}
    ]
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        messages=messages,
        temperature=0
    )
    return response.choices[0].message.content.strip()

def retry_query_with_error_context(bad_query, error_message, schema_context, user_question):
    messages = [
        {"role": "system", "content": f"""You are a helpful assistant that corrects broken SQL queries.
            You are given a faulty SQL query, the schema, the user's natural language question, and the error message produced by the database.
            Fix the query to resolve the error. Only use SELECT statements. Do not use DELETE, INSERT, UPDATE, etc.

            Schema:
            {schema_context}

            Make sure:
            - Only valid table and column names from the schema are used.
            - Aliases are not mixed up.
            - Joins are valid.
            - Quotes around identifiers are correct (use double quotes).
            - The corrected query answers the user's question.

            Return ONLY the corrected SQL query, no explanations or comments."""},
                    {"role": "user", "content": f"""User question: {user_question}
            Broken query:
            {bad_query}

            Error message:
            {error_message}
            """}
            ]
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        messages=messages,
        temperature=0
    )
    return response.choices[0].message.content.strip()

# === 6b. Suggest follow-up questions ===
def suggest_followup_questions(schema_context, user_question):
    messages = [
        {"role": "system", "content": f"""You are a data expert suggesting further exploratory data questions for a user working with a SQL-accessible relational database. Based on the schema:\n{schema_context}\n"""},
        {"role": "user", "content": f"""User asked: "{user_question}". Suggest 3 follow-up questions the user might want to ask next to better explore trends, patterns in the data."""}
    ]
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        messages=messages,
        temperature=0
    )
    return response.choices[0].message.content.strip()


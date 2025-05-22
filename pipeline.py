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
# openai.api_key = "your-openai-key"
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
def run_sql(query, conn):
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return {"columns": columns, "rows": rows}

# === 5. Ask GPT for query generation ===
def generate_sql(user_question, schema_context):
    messages = [
        {"role": "system", "content": f"""You are an assistant that generates SQL queries return only query itself without any comments. Use only SELECT and try to use only meaningful columns such as name, not just ids, use joins when necessary and do not confuse table name aliases. You can order the results to return the most informative data in the database.
Never query for all columns from a table. You must query only the columns that are needed to answer the question. Wrap each column name in double quotes (") to denote them as delimited identifiers.
Pay attention to use only the column names you can see in the tables below. Be careful to not query for columns that do not exist in your selecting table. Also, pay attention to which column is in which table.
Pay attention to use CURRENT_DATE function to get the current date, if the question involves "today". Schema:\n{schema_context} pickup location table is one to one with locations table, if you want to get pickuplocation name, join with location and get locaiton.name instead. If user asks for map plot, always select lon and lat """},
        {"role": "user", "content": user_question}
    ]
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0
    )
    return response.choices[0].message.content.strip()

# === 6. Generate Plotly JSON ===
def generate_plotly_json(data, user_question):
    messages = [
        {"role": "system", "content": (
    "You are a visualization expert. Given a dataset and user question, generate a JSON using Plotly "
    "(plotly.graph_objects or plotly.express) to create the most appropriate and insightful chart. "
    "Return ONLY the fig.to_json() output, not HTML or explanations.\n\n"

    "Use chart types based on data patterns:\n"
    "- Use bar or horizontal bar if comparing categories (e.g., top N).\n"
    "- Use pie if data has a part-of-whole relationship.\n"
    "- Use 'scatter' with mode='lines+markers' for time series or trends.\n"
    "- Use scatter if comparing two continuous variables.\n"
    "- Use map subplots if you want to show something on it instead of mapbox since it is deprecated"
    "- Use heatmap if showing relationships between 2 categorical axes with a metric.\n\n"

    "If the user question is unclear or missing, analyze the dataset and choose the most informative and insightful chart. "
    "For time-based job/task data, consider showing:\n"
    "- Duration between fact_start_at and fact_end_at per job.\n"
    "- Comparison of planned vs actual start/end.\n"
    "- Delays or time gaps between planned and actual.\n\n"

    "Always add axis titles, legends, and clear chart titles and unquote integers when you are referring to colours of plot."
)},
        {"role": "user", "content": f"The user asked: '{user_question}'. Here is the data:\nColumns: {data['columns']}\nRows: {data['rows']}"}
    ]
    print(f"Columns: {data['columns']}\nRows: {data['rows']}")

    response = openai.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        messages=messages,
        temperature=0
    )
    # return response.choices[0].message.content.strip()
    json_out = response.choices[0].message.content.strip()
    if '"data": []' in json_out:
        print("Empty chart data returned from GPT.")
        # Optional: trigger fallback visualization or re-ask GPT with more context
    return json_out

def generate_plot_code(data, user_question):
    messages = [
        {"role": "system", "content": (
            "You are a Python data visualization assistant.\n"
            "Given structured data as a dict with 'columns' and 'rows', generate Python code "
            "that defines a function called `create_figure(data: dict) -> plotly.graph_objects.Figure`.\n\n"
            "Use `plotly.graph_objects` or `plotly.express` to build a clear, beautiful, and relevant chart "
            "based on the question and the data.\n"
            "The function should not read files or require external input.\n"
            "Return ONLY the code (no explanation, no markdown)."
        )},
        {"role": "user", "content": f"The user asked: '{user_question}'.\nHere is the data:\nColumns: {data['columns']}\nRows: {data['rows']}"}
    ]
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
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
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0
    )
    return response.choices[0].message.content.strip()


# === 7. Full pipeline ===
def handle_user_query(user_question):
    conn = get_connection()
    schema = get_schema_with_samples(conn)
    print(schema)
    sql_query = generate_sql(user_question, schema).replace("`","").replace("sql","")
    print("Generated SQL:", sql_query)

    if not is_safe_sql(sql_query):
        raise Exception("Unsafe or prohibited SQL query.")

    data = run_sql(sql_query, conn)
    print(data)
    # plotly_json = generate_plotly_json(data, user_question).replace("`","").replace("json","").replace("mapbox","map")
    # print("Plotly json:", plotly_json)
    suggestions = suggest_followup_questions(schema, user_question)
    print("\n💡 Follow-up questions you might ask:\n" + suggestions)
    conn.close()
    # return orjson.loads(plotly_json)
    return fig

# === Example usage ===
if __name__ == "__main__":
    # user_question = "What are the top 5 drivers by amount of routes?"
    while True:
        user_question = input("What data you want to get: ")
        if user_question == 'q':
            break
        try:
            fig = handle_user_query(user_question)
            # fig = pio.from_json(orjson.dumps(fig_json))
            fig.show()  # Optional, for debugging
        except Exception as e:
            print("Error:", e)

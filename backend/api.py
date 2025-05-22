# api_server.py

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import orjson

from pipeline import *
# Assuming the existing logic is available (your whole script above)
# from your_module import get_connection, generate_sql, is_safe_sql, run_sql, get_schema_with_samples

app = FastAPI()

# Allow frontend access (adjust for your domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to specific frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QuestionRequest(BaseModel):
    question: str

@app.post("/ask")
def ask_data(req: QuestionRequest):
    try:
        conn = get_connection()
        schema = get_schema_with_samples(conn)
        sql_query = generate_sql(req.question, schema).replace("`", "").replace("sql", "")

        print(req.question)
        print(sql_query)
        
        if not is_safe_sql(sql_query):
            return {"error": "Unsafe or invalid query generated."}
        
        data = run_sql(sql_query, conn)
        print(data)
        conn.close()
        chart = transform_to_chartjs_format(data)
        print(chart)
        # Transform data to Chart.js format
        return chart

    except Exception as e:
        return {"error": str(e)}

def transform_to_chartjs_format(data):
    # Try simple bar or line chart format
    if not data["rows"]:
        return {"labels": [], "datasets": []}

    labels = [str(row[0]) for row in data["rows"]]
    datasets = []

    for i, col in enumerate(data["columns"][1:]):
        datasets.append({
            "label": col,
            "data": [row[i+1] for row in data["rows"]],
            "backgroundColor": f"rgba(54, 162, 235, 0.6)",
            "borderColor": f"rgba(54, 162, 235, 1)",
            "borderWidth": 1
        })

    return {
        "labels": labels,
        "datasets": datasets
    }

# api_server.py

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

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
        user_question = req.question
        conn = get_connection()
        schema = get_schema_with_samples(conn)
        sql_query = generate_sql(user_question, schema).replace("`", "").replace("sql", "")

        print(user_question)
        print(sql_query)

        if not is_safe_sql(sql_query):
            return {"error": "Unsafe or invalid query generated."}
        
        data = run_sql(sql_query, conn, schema_context=schema, user_question=user_question)
        print(data)
        conn.close()

        rows_as_dict = [dict(zip(data["columns"], row)) for row in data["rows"]]
        print(rows_as_dict)
        response_data = {
            "columns": data["columns"],
            "rows": rows_as_dict
        }

        return Response(content=orjson.dumps(response_data), media_type="application/json")

    except Exception as e:
        return Response(content=orjson.dumps({"error": str(e)}), media_type="application/json")

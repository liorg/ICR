FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# תג ה-build — מוחזר ב-GET /version.
#
# חייב להיות אחרי COPY: כל ARG שמשתנה מבטל את ה-layer cache של כל מה
# שבא אחריו. כאן הוא בסוף, אז שינוי תג לא גורם ל-pip install מחדש.
ARG BUILD_TAG=unknown
ENV BUILD_TAG=$BUILD_TAG

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

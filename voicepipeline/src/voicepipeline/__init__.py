def main() -> None:
    import uvicorn

    uvicorn.run("voicepipeline.api:app", host="127.0.0.1", port=8001, reload=False)

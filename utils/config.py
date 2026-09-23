from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    aws_access_key_id: str = Field(..., description="AWS Access Key ID")
    aws_secret_access_key: str = Field(..., description="AWS Secret Access Key")
    aws_region: str = Field(..., description="AWS Region Code")
    aws_endpoint_url: str = Field(..., description="AWS Endpoint URL for S3/Drive")
    s3_pdf_bucket: str = Field(..., description="Bucket holding the uploaded PDFs")
    s3_parsed_mds: str = Field(..., description="Bucket holding the parsed markdown files")
    temp_pd_dir: str = Field(..., description="Local root directory for scratch files")
    temp_pdf_folder: str = Field(..., description="Sub-folder of TEMP_PD_DIR for PDFs")
    temp_md_folder: str = Field(..., description="Sub-folder of TEMP_PD_DIR for markdown")
    api_host: str = Field("0.0.0.0", description="Host the FastAPI server binds to")
    api_port: int = Field(8000, description="Port the FastAPI server listens on")
    temporal_host: str = Field("localhost:7233", description="host:port of the Temporal frontend service")
    temporal_namespace: str = Field("default", description="Temporal namespace the worker and client use")
    temporal_task_queue: str = Field("process_pdf_queue", description="Task queue the workflow and activities are polled from")
    log_level: str = Field("INFO", description="Root log level: DEBUG, INFO, WARNING, ERROR")
    run_worker_in_api: bool = Field(False, description="Run the Temporal worker inside the API process")

    # LLM
    llm_provider: str = Field("openrouter", description="Which LLMInterface implementation the factory returns")
    llm_timeout_seconds: float = Field(300, description="Per-call timeout for an LLM request")
    llm_max_tokens: int = Field(16000, description="Maximum tokens the model may generate")
    llm_temperature: float = Field(0.2, description="Sampling temperature; legal advice wants determinism")
    openrouter_api_key: str = Field("", description="OpenRouter API key")
    openrouter_model: str = Field("deepseek/deepseek-v4-flash", description="OpenRouter model id")
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", description="OpenRouter API base URL")

    # legal advice pipeline
    s3_legal_advice: str = Field("legaladvice", description="Bucket the advice JSON lands in")
    s3_projects: str = Field("projects", description="Bucket holding everything that belongs to a project")
    legal_task_queue: str = Field("legal_advice_queue", description="Task queue for the legal review workflow")
    legal_max_concurrent_pdfs: int = Field(10, description="How many PDFs the workflow processes at once")
    legal_pages_per_batch: int = Field(30, description="Pages per LLM call")
    legal_max_pdfs: int = Field(20, description="Most PDFs accepted in one request")
    max_upload_bytes: int = Field(25 * 1024 * 1024, description="Largest single PDF the service accepts")
    max_request_bytes: int = Field(100 * 1024 * 1024, description="Largest total upload in one request")
    human_input_timeout_seconds: float = Field(3600, description="How long to wait for a human before continuing")

    # emailed reports; with no SMTP_HOST the review simply does not send one
    smtp_host: str = Field("", description="SMTP server the report is sent through")
    smtp_port: int = Field(587, description="SMTP port; 587 for STARTTLS, 25 for a local relay")
    smtp_username: str = Field("", description="SMTP username; empty for a relay that needs no login")
    smtp_password: str = Field("", description="SMTP password")
    smtp_from: str = Field("", description="Address the report is sent from")
    smtp_use_tls: bool = Field(True, description="Upgrade the connection with STARTTLS")
    smtp_timeout_seconds: float = Field(30, description="Timeout for the SMTP conversation")

    # voice: the ASR and TTS models run in their own process (voice/)
    voice_service_url: str = Field("http://127.0.0.1:8100", description="Base URL of the voice service")
    voice_timeout_seconds: float = Field(120, description="Timeout for a transcription or a synthesis")
    asr_provider: str = Field("voice_service", description="Which ASRInterface implementation the factory returns")
    tts_provider: str = Field("voice_service", description="Which TTSInterface implementation the factory returns")
    tts_voice: str = Field("Ryan", description="Which of the service's voices speaks the answers")
    tts_language: str = Field("English", description="Language hint for both transcription and synthesis")
    chat_context_characters: int = Field(12000, description="How much document text one chat answer may be given")
    store_audio: bool = Field(True, description="Keep the recordings in the bucket beside the chat thread")

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("*", mode="after")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        """
        Cleans up values that arrive with stray spaces or quotes.

        python-dotenv strips surrounding quotes, docker's --env-file does not,
        so the same .env line can reach us either way.
        """
        if not isinstance(value, str):
            return value

        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()

        return value

    @property
    def temp_root(self) -> Path:
        # anchored to the project root so the scratch dirs do not follow the cwd
        return Path(__file__).parent.parent / self.temp_pd_dir

    @property
    def temp_pdf_path(self) -> Path:

        path = self.temp_root / self.temp_pdf_folder

        # maek sure tha path exits
        path.mkdir(parents=True, exist_ok=True)

        return path

    @property
    def temp_md_path(self) -> Path:

        path = self.temp_root / self.temp_md_folder

        # maek sure tha path exits
        path.mkdir(parents=True, exist_ok=True)

        return path


_settings_instance = None


def get_setting() -> Settings:
    """
    Returns a singleton instance of the Settings object,
    automatically loaded from the .env file by Pydantic.
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance

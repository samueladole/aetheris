# Project Constitution: Financial Intelligence Knowledge Graph & Sentiment Analysis Engine (Aetheris)

## Core Principles & Governance

### I. Strict Open-Source Compliance
- **Zero Commercial Dependencies:** The project must rely strictly on open-source software (OSS) licensed under permissive terms (e.g., MIT, Apache 2.0). 
- **No Paid APIs or Proprietary Tiers:** All data ingestion must utilize official free tiers (FRED, Alpha Vantage, Finnhub, Financial Modeling Prep, Tiingo) or public open datasets. Commercial orchestration or proprietary SaaS observability tools are strictly prohibited.

### II. Decoupled Modular Architecture
- **Isolated Boundaries:** Each system layer—Ingestion, Caching, Sentiment Analysis, Knowledge Graph, and Orchestration—must operate as an independent, loosely coupled module.
- **Reusability:** Core components and workers must be designed for portability, allowing them to be imported seamlessly into future autonomous agentic workflows or independent projects.

### III. Resilient Durable Execution
- **State Checkpointing & Recovery:** All pipeline workflows must be executed as durable state machines capable of automatic recovery, retries with exponential backoff, and failure compensation from the exact point of failure.
- **Resilient Network Boundaries:** External API interactions must incorporate robust rate limiting, token bucket queues, and immutable local caching to handle quota limits gracefully.

### IV. Comprehensive Observability & Evaluation
- **End-to-End Tracing:** Every workflow execution, ingestion fetch, sentiment model inference, and graph mutation must emit distributed trace spans and performance metrics.
- **Continuous Evaluation:** Model outputs, confidence scores, and data drift must be logged and evaluated using open-source telemetry frameworks.

### V. Specification-Driven Rigor
- **Code Follows Spec:** No code or infrastructure component shall be written without a corresponding specification update or task breakdown mapping directly to project architecture goals.
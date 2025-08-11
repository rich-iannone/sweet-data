![Sweet: Interactive Data Engineering CLI](sweet-logo.svg)

_Fun, interactive data manipulation in your terminal_

<div align="left">

[![Python Versions](https://img.shields.io/pypi/pyversions/sweet-data.svg)](https://pypi.python.org/pypi/sweet-data)
[![PyPI](https://img.shields.io/pypi/v/sweet-data)](https://pypi.org/project/sweet-data/#history)
[![PyPI Downloads](https://static.pepy.tech/badge/sweet-data)](https://pepy.tech/projects/sweet-data)
[![License](https://img.shields.io/github/license/rich-iannone/sweet-data)](https://img.shields.io/github/license/rich-iannone/sweet-data)

[![CI Build](https://github.com/rich-iannone/sweet-data/actions/workflows/ci.yaml/badge.svg)](https://github.com/rich-iannone/sweet-data/actions/workflows/ci.yaml)
[![Repo Status](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-v2.1%20adopted-ff69b4.svg)](https://www.contributor-covenant.org/version/2/1/code_of_conduct.html)

</div>

Sweet is a speedy and fun terminal-based data manipulation tool that transforms how you work with tabular data. Think of it as Excel or Google Sheets in your terminal, but with superpowers: featuring an intelligent AI assistant that understands natural language, you can now ask questions about your data and request transformations using plain English, all without leaving your terminal!

Beyond AI assistance, Sweet offers intuitive tools for quick edits: click to modify cells, add/remove columns, change data types, sort, filter, and explore your data with real-time feedback. Save your changes easily to many different formats, including CSV, JSON, and Parquet.

Sweet is for data scientists, engineers, and developers who want the familiar convenience of spreadsheet-like editing combined with powerful AI assistance and programmatic data manipulation. Work conversationally with your data or make quick manual edits, all right in your terminal or IDE.

## See Sweet in Action

### Loading Data and Making Changes

![Sweet: Loading data and editing values](assets/sweet-open-load-data-change-values.gif)

### Modifying Rows and Columns

![Sweet: Modifying rows and columns](assets/sweet-modify-rows-and-columns.gif)

### Working with Column Types and Saving

![Sweet: Changing column types and saving data](assets/change-column-type-save-data.gif)

### Polars Data Manipulation

![Sweet: Loading data and modifying with Polars](assets/load-data-modify-with-polars.gif)

### Copy-Paste from Web Sources

![Sweet: Copy-paste data from Wikipedia](assets/copy-paste-from-wikipedia.gif)

## Getting Started in 30 Seconds

```bash
# Install and launch Sweet
pip install sweet-data
sweet
```

Once Sweet opens:

1. **Load sample data**: use the file browser to load your CSV files or paste data directly from spreadsheets
2. **Try making edits**: click on cells to edit values, add/remove columns, or change data types
3. **Use the AI Assistant**: ask questions like "What does this data show?" or "Filter rows where age > 30" and watch Sweet generate the code
4. **See instant results**: Sweet shows you exactly what Polars transformation code will be applied through an approval workflow

The AI Assistant is Sweet's premier feature: it can help you explore your data, explain patterns, and generate Polars transformations using natural language. Just type what you want to do and let Sweet handle the code!

## Why Choose Sweet?

- **Interactive terminal interface**: modern TUI with syntax highlighting and real-time feedback
- **Intuitive navigation**: use keyboard shortcuts or mouse/pointer interactions for smooth control
- **Flexible data loading**: files, stdin piping, or paste data directly from spreadsheets/web tables
- **Multiple export formats**: save your transformed data as CSV, TSV, Parquet, JSON, or JSONL
- **Accessible**: refine smaller datasets with ease or tackle complicated transformations without hassle
- **Fast operations**: built on Polars for high-performance data processing
- **AI-powered insights**: leverage advanced language models for data exploration and transformation

## Installation

You can install Sweet using pip:

```bash
pip install sweet-data
```

## Sweet AI Assistant

Sweet includes an intelligent AI assistant that transforms how you work with data. This powerful feature uses advanced language models to help you explore, understand, and transform your datasets through natural language interactions.

### AI Assistant in Action

![Sweet: AI-powered data discussion and transformation](assets/ai-data-discuss-transform.gif)

### AI-Powered Data Exploration

- **Conversational Analysis**: ask questions like "What columns do we have?", "Describe this dataset", or "What are the data types?" and get instant, intelligent responses
- **Smart Data Insights**: get contextual analysis and explanations about your data's structure, patterns, and characteristics
- **Interactive Guidance**: receive helpful suggestions and explanations as you work through your data analysis workflow

### Intelligent Code Generation

- **Natural Language to Code**: request transformations like "Add a bonus column that's 30% of salary" or "Filter rows where age is greater than 25" and get working Polars code
- **Comprehensive Polars Support**: the AI assistant has deep knowledge of the entire Polars API, including advanced operations like rolling windows, string processing, and datetime manipulations
- **Context-Aware Suggestions**: code generation takes into account your actual column names, data types, and dataset structure

### Key AI Features

- **Dual Mode Operation**: automatically switches between conversational analysis and code generation based on your needs
- **Real-time Context**: the AI assistant understands your current dataset structure and provides relevant, specific advice
- **Multiple LLM Support**: works with popular language model providers including Anthropic Claude and OpenAI GPT
- **Conversational Context**: maintains conversation context to provide better assistance throughout your data exploration session

To use the AI assistant, simply type your questions or requests in natural language, and Sweet will provide intelligent responses, explanations, or generate the appropriate Polars code for your transformations.

### Setup Requirements

To enable the AI assistant, you'll need to set up API keys for your preferred language model provider in a `.env` file in your working directory:

```bash
# For Anthropic Claude (recommended)
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Or for OpenAI GPT
OPENAI_API_KEY=your_openai_api_key_here
```

Sweet will automatically detect and use available API keys, with Anthropic Claude preferred when both are present.

_The AI assistant is powered by [chatlas](https://posit-dev.github.io/chatlas/), a powerful Python package that provides seamless integration with multiple language model providers._

## Features That Set Sweet Apart

- **Complete exploration workflow**: from data loading to transformation to results visualization in a single interface
- **Built for experimentation**: interactive environment perfect for data exploration and hypothesis testing
- **Practical outputs**: get exactly what you need: transformed data, clear results, and transformation tracking
- **Flexible deployment**: use for quick exploration or as a foundation for building data workflows
- **Modern interface**: terminal-based UI with syntax highlighting, keyboard shortcuts, and mouse support for intuitive navigation

## Technical Details & Acknowledgments

Sweet is built on modern Python libraries for optimal performance and developer experience. We're grateful to the maintainers and contributors of these foundational projects:

- **[Polars](https://github.com/pola-rs/polars)**: The blazingly fast DataFrame library that powers all data operations in Sweet
- **[Textual](https://github.com/Textualize/textual)**: The incredible TUI framework that makes Sweet's interactive interface possible
- **[chatlas](https://posit-dev.github.io/chatlas/)**: The elegant library that enables Sweet's AI assistant capabilities with LLM provider integration
- **[Rich](https://github.com/Textualize/rich)**: Terminal formatting and beautiful display components
- **[Click](https://github.com/pallets/click)**: Command-line interface for clean CLI integration

The application architecture separates data models from UI components, making it extensible and maintainable. A huge thank you to all the developers who created these ultra-powerful, well-designed tools that make Sweet possible!

## Contributing to Sweet

There are many ways to contribute to the ongoing development of Sweet. Some contributions can be simple (like fixing typos, improving documentation, filing issues for feature requests or problems, etc.) and others might take more time and care (like answering questions and submitting PRs with code changes). Just know that anything you can do to help would be very much appreciated!

## Roadmap

We're actively working on enhancing Sweet with:

1. **Transformation history tracking**: Export transformation history as clean Python/Polars scripts for reproducible workflows
2. Additional data format support (Excel, JSON, Arrow, etc.)
3. Advanced transformation templates and snippets
4. Integration with cloud data sources
5. Export to multiple formats and destinations
6. Enhanced branching and workflow management
7. **Enhanced AI capabilities**: Expanding the AI assistant with more sophisticated analysis and visualization suggestions

If you have any ideas for features or improvements, don't hesitate to share them with us! We are always looking for ways to make Sweet better.

## Code of Conduct

Please note that the sweet-data project is released with a [contributor code of conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). <br>By participating in this project you agree to abide by its terms.

## 📄 License

sweet-data is licensed under the MIT license.

© sweet-data authors

## 🏛️ Governance

This project is primarily maintained by
[Rich Iannone](https://bsky.app/profile/richmeister.bsky.social). Other authors may occasionally
assist with some of these duties.

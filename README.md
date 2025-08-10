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

Sweet is a speedy and fun terminal-based data manipulation tool that transforms how you work with tabular data. With its intuitive interface and real-time feedback, you can quickly explore, sort, and make changes to your data. Plus you can do more advanced things like transforming data using interactive Polars expressions.

Sweet is for data scientists, engineers, and developers who want to explore and edit tabular data interactively. And they can do it right in their terminal or IDE, without leaving their coding workflow.

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

```python
# Launch Sweet from the command line
sweet

# Or load data directly
sweet --file data.csv

# Or pipe data in
echo "data.csv" | sweet
cat data.csv | sweet
```

Once in Sweet's interactive interface:

1. Load your data using the file browser or paste tabular data directly
2. Write Polars expressions with syntax highlighting: `df = df.filter(pl.col("age") > 25)`
3. See results instantly in the data preview with **approval workflow** - Sweet shows you exactly what will change before applying transformations
4. **AI-assisted transformations** - Ask the AI assistant for help and review generated code before execution

## Why Choose Sweet?

- **Interactive terminal interface**: Modern TUI with syntax highlighting and real-time feedback
- **Intuitive navigation**: Use keyboard shortcuts or mouse/pointer interactions for smooth control
- **Experimental workflow**: Interactive environment perfect for data exploration and hypothesis testing
- **Flexible data loading**: Files, stdin piping, or paste data directly from spreadsheets/web tables
- **Multiple export formats**: Save your transformed data as CSV, TSV, Parquet, JSON, or JSONL
- **Fast operations**: Built on Polars for high-performance data processing
- **Accessible**: Perfect for both small datasets and quick exploration tasks

## Real-World Example

```bash
# Start with a CSV file
sweet --file sales_data.csv

# In Sweet's interface, build transformations step by step:
# 1. Filter recent sales
df = df.filter(pl.col("date") > pl.date(2024, 1, 1))

# 2. Calculate revenue
df = df.with_columns((pl.col("price") * pl.col("quantity")).alias("revenue"))

# 3. Group by category
df = df.group_by("category").agg([
    pl.col("revenue").sum().alias("total_revenue"),
    pl.col("quantity").sum().alias("total_quantity")
])

# See results immediately in the data preview
```

The interactive interface lets you experiment with different approaches and see results instantly, making data exploration both efficient and enjoyable.

## Installation

You can install Sweet using pip:

```bash
pip install sweet-data
```

## Usage

```bash
# Launch the interactive application
sweet

# Load a specific data file
sweet --file data.csv

# Pipe filename as a string (note the echo command)
echo "data.csv" | sweet

# Or pipe file content directly
cat data.csv | sweet
```

## Sweet AI Assistant

Sweet includes an intelligent AI assistant that transforms how you work with data. This powerful feature uses advanced language models to help you explore, understand, and transform your datasets through natural language interactions.

### AI Assistant in Action

![Sweet: AI-powered data discussion and transformation](assets/ai-data-discuss-transform.gif)

### AI-Powered Data Exploration

- **Conversational Analysis**: Ask questions like "What columns do we have?", "Describe this dataset", or "What are the data types?" and get instant, intelligent responses
- **Smart Data Insights**: Get contextual analysis and explanations about your data's structure, patterns, and characteristics
- **Interactive Guidance**: Receive helpful suggestions and explanations as you work through your data analysis workflow

### Intelligent Code Generation

- **Natural Language to Code**: Request transformations like "Add a bonus column that's 30% of salary" or "Filter rows where age is greater than 25" and get working Polars code
- **Comprehensive Polars Support**: The AI assistant has deep knowledge of the entire Polars API, including advanced operations like rolling windows, string processing, and datetime manipulations
- **Context-Aware Suggestions**: Code generation takes into account your actual column names, data types, and dataset structure

### Key AI Features

- **Dual Mode Operation**: Automatically switches between conversational analysis and code generation based on your needs
- **Real-time Context**: The AI assistant understands your current dataset structure and provides relevant, specific advice
- **Multiple LLM Support**: Works with popular language model providers including Anthropic Claude and OpenAI GPT
- **Conversational Context**: Maintains conversation context to provide better assistance throughout your data exploration session

To use the AI assistant, simply type your questions or requests in natural language, and Sweet will provide intelligent responses, explanations, or generate the appropriate Polars code for your transformations.

_The AI assistant is powered by [chatlas](https://posit-dev.github.io/chatlas/), a powerful Python package that provides seamless integration with multiple language model providers._

## Features That Set Sweet Apart

- **Complete exploration sworkflow**: From data loading to transformation to results visualization in a single interface
- **Built for experimentation**: Interactive environment perfect for data exploration and hypothesis testing
- **Practical outputs**: Get exactly what you need: transformed data, clear results, and transformation tracking
- **Flexible deployment**: Use for quick exploration or as a foundation for building data workflows
- **Modern interface**: Terminal-based UI with syntax highlighting, keyboard shortcuts, and mouse support for intuitive navigation
- **No vendor lock-in**: Uses standard Polars expressions that work in any Python environment

## Technical Details & Acknowledgments

Sweet is built on modern Python libraries for optimal performance and developer experience. We're grateful to the maintainers and contributors of these foundational projects:

- **[Polars](https://github.com/pola-rs/polars)**: The blazingly fast DataFrame library that powers all data operations in Sweet
- **[Textual](https://github.com/Textualize/textual)**: The incredible TUI framework that makes Sweet's interactive interface possible
- **[chatlas](https://posit-dev.github.io/chatlas/)**: The elegant library that enables Sweet's AI assistant capabilities with LLM provider integration
- **Rich**: Terminal formatting and beautiful display components
- **Click**: Command-line interface for clean CLI integration

The application architecture separates data models from UI components, making it extensible and maintainable. A huge thank you to all the developers who created these powerful, well-designed tools that make Sweet possible!

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

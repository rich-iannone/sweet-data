# Sweet: Interactive Data Engineering CLI

Sweet is a terminal-based data manipulation tool that bridges the gap between spreadsheets and code. Execute Polars expressions interactively while automatically generating reproducible Python code for your data transformations.

## Key Benefits

- **Interactive**: Work with data in a modern terminal interface with syntax highlighting
- **Reproducible**: Every transformation generates reusable Polars code
- **Flexible**: Load data via command line, file paths, stdin piping, or paste tabular data directly
- **Fast**: Built on Polars for high-performance data operations
- **Accessible**: Perfect for small datasets and quick data exploration tasks
- **Branching**: Experiment with different transformation paths without losing work

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/rich-iannone/sweet.git
cd sweet

# Install in development mode
pip install -e ".[dev]"
```

### Usage

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

## How It Works

Sweet provides an interactive terminal interface where you can:

1. **Load Data**: Import CSV, JSON, Parquet files, pipe filenames or content from stdin, or paste tabular data from spreadsheets/web tables
2. **Transform**: Write Polars expressions with syntax highlighting and instant feedback
3. **Explore**: View data transformations in real-time with automatic table updates
4. **Generate**: Export your work as reproducible Python/Polars code
5. **Branch**: Create parallel transformation workflows for experimentation

The application automatically tracks your transformation history and generates clean, reusable code that you can integrate into your data pipelines. Perfect for ad-hoc analysis of small datasets or data copied from various sources.

## Development

### Project Structure

```
sweet/
├── core/               # Data models and transformation engine
│   ├── workbook.py    # Workbook and Sheet classes
│   └── transforms.py  # Expression evaluation and code generation
├── ui/                # Terminal interface components
│   ├── app.py        # Main application
│   └── widgets.py    # Custom UI widgets
└── cli.py            # Command-line interface
```

### Development Commands

```bash
# Run tests
make test

# Run linting and formatting
make quality

# Run the application in development mode
python -m sweet
```

## Dependencies

- **Textual**: Modern terminal UI framework
- **Polars**: High-performance DataFrame library
- **Rich**: Terminal formatting and display
- **Click**: Command-line interface

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes and run tests (`make quality`)
4. Commit your changes (`git commit -m 'Add amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
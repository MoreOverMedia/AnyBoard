use std::ffi::CString;

use pyo3::Python;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    const PYTHON_SOURCE: &str = include_str!("../boardbot.py");
    let python_source = CString::new(PYTHON_SOURCE)?;

    Python::attach(move |py| py.run(&python_source, None, None))?;

    Ok(())
}

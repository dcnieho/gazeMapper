[![Downloads](https://static.pepy.tech/badge/gazeMapper)](https://pepy.tech/project/gazeMapper)
[![Citation Badge](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.juleskreuer.eu%2Fcitation-badge.php%3Fshield%26doi%3D10.3758%2Fs13428-023-02105-5&color=blue)](https://scholar.google.com/citations?view_op=view_citation&citation_for_view=uRUYoVgAAAAJ:uWQEDVKXjbEC)
[![PyPI Latest Release](https://img.shields.io/pypi/v/gazeMapper.svg)](https://pypi.org/project/gazeMapper/)
[![image](https://img.shields.io/pypi/pyversions/gazeMapper.svg)](https://pypi.org/project/gazeMapper/)

# gazeMapper v0.5.0
Tool for automated world-based analysis of wearable eye tracker data.

If you use this tool or any of the code in this repository, please cite:<br>
Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. and Hooge, I.T.C. (in prep). gazeMapper: A tool for automated world-based analysis of wearable eye tracker data

# How to acquire
GlassesValidator is available from `https://github.com/dcnieho/gazeMapper`, and supports Python 3.10 and 3.11 on Windows, MacOS and Linux.

For Windows users who wish to use the gazeMapper GUI, the easiest way to acquire gazeMapper is to [download
a standalone executable](https://github.com/dcnieho/gazeMapper/releases/latest). The standalone executable is not
available for MacOS or Linux.

For users on Windows, Mac or Linux who wish to use gazeMapper in their Python code, the easiest way to acquire
gazeMapper is to install it directly into your Python distribution using the command
`python -m pip install gazeMapper`. Should that fail, this repository is pip-installable as well:
`python -m pip install git+https://github.com/dcnieho/gazeMapper.git#egg=gazeMapper`. NB: on some platforms you may have
to replace `python` with `python3` in the above command lines.

Once pip-installed in your Python distribution, there are three ways to run the GUI on any of the supported operating systems:
1. Directly in the terminal of your operating system, type `gazeMapper` and run it.
2. Open a Python console. From such a console, running the GUI requires only the following two lines of code:
    ```python
    import gazeMapper
    gazeMapper.GUI.run()
    ```
3. If you run the gazeMapper's GUI from a script, make sure to wrap your script in `if __name__=="__main__"`. This is required for correct operation from a script because the GUI uses multiprocessing functionality. Do as follows:
    ```python
    if __name__=="__main__":
        import gazeMapper
        gazeMapper.GUI.run()
    ```

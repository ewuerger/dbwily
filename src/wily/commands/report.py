"""
Report command.

The report command gives a table of metrics for a specified list of files.
Will compare the values between revisions and highlight changes in green/red.
"""
import typing as T
from json import dumps
from pathlib import Path
from shutil import copytree
from string import Template

import tabulate
from wily import MAX_MESSAGE_WIDTH, format_date, format_delta, format_revision, logger
from wily.config import WilyConfig
from wily.helper.custom_enums import ReportFormat
from wily.lang import _
from wily.operators import BAD_COLORS, GOOD_COLORS, resolve_metric_as_tuple
from wily.state import State


def report(
    config: WilyConfig,
    path: Path,
    metrics: str,
    n: int,
    output: Path,
    include_message: bool = False,
    format: ReportFormat = ReportFormat.CONSOLE,
    console_format: str = None,
) -> None:
    """
    Show information about the cache and runtime.

    :param config: The configuration
    :type  config: :class:`wily.config.WilyConfig`

    :param path: The path to the file
    :type  path: ``str``

    :param metrics: Name of the metric to report on
    :type  metrics: ``str``

    :param n: Number of items to list
    :type  n: ``int``

    :param output: Output path
    :type  output: ``Path``

    :param include_message: Include revision messages
    :type  include_message: ``bool``

    :param format: Output format
    :type  format: ``ReportFormat``

    :param console_format: Grid format style for tabulate
    :type  console_format: ``str``
    """
    logger.debug("Running report command")
    logger.info(f"-----------History for {metrics}------------")

    data = []
    metric_metas = []

    for metric in metrics:
        operator, metric = resolve_metric_as_tuple(metric)
        # Set the delta colors depending on the metric type
        metric_meta = {
            "key": metric.name,
            "operator": operator.name,
            "title": metric.description,
            "type": metric.type,
            "measure": metric.measure,
        }
        metric_metas.append(metric_meta)

    state = State(config)
    for archiver in state.archivers:
        history = state.index[archiver].revisions[:n][::-1]
        last = {}
        for rev in history:
            vals = []
            for meta in metric_metas:
                try:
                    logger.debug(
                        f"Fetching metric {meta['key']} for {meta['operator']} in {path}"
                    )
                    val = rev.get(config, archiver, meta["operator"], path, meta["key"])
                    last_val = last.get(meta["key"], None)
                    # Measure the difference between this value and the last
                    if meta["type"] in (int, float):
                        delta = val - last_val if last_val else 0
                        change = delta
                    elif last_val:
                        delta = ord(last_val) - ord(val) if last_val != val else 1
                        change = last_val
                    else:
                        delta = 1
                        change = val

                    last[meta["key"]] = val
                    if delta == 0:
                        delta_col = delta
                    elif delta < 0:
                        delta_col = _plant_delta_color(
                            BAD_COLORS[meta["measure"]], change
                        )
                    else:
                        delta_col = _plant_delta_color(
                            GOOD_COLORS[meta["measure"]], change
                        )
                    k = _plant_delta(val, delta_col)
                except KeyError as e:
                    k = f"Not found {e}"
                vals.append(k)
            if include_message:
                data.append(
                    (
                        format_revision(rev.revision.key),
                        rev.revision.message[:MAX_MESSAGE_WIDTH],
                        rev.revision.author_name,
                        format_date(rev.revision.date),
                        *vals,
                    )
                )
            else:
                data.append(
                    (
                        format_revision(rev.revision.key),
                        rev.revision.author_name,
                        format_date(rev.revision.date),
                        *vals,
                    )
                )
    descriptions = [meta["title"] for meta in metric_metas]
    if include_message:
        headers = (_("Revision"), _("Message"), _("Author"), _("Date"), *descriptions)
    else:
        headers = (_("Revision"), _("Author"), _("Date"), *descriptions)

    if format in FORMAT_MAP:
        FORMAT_MAP[format](path, output, data, headers)
        return

    print(
        tabulate.tabulate(
            headers=headers, tabular_data=data[::-1], tablefmt=console_format
        )
    )


def _plant_delta(val: T.Union[str, int], last_val: T.Union[str, int]) -> str:
    now = format_delta(val)
    then = format_delta(last_val)
    return " ".join((now, f"({then})"))


def _plant_delta_color(color: int, change: T.Union[str, int]) -> str:
    end = format_delta(change)
    if (isinstance(change, int) or isinstance(change, float)) and change > 0:
        end = "+" + end

    return "".join((f"\u001b[{color}m", f"{end}\u001b[0m"))


def generate_html_report(
    path: Path, output: Path, data: T.List[T.Tuple[str]], headers: T.Tuple[str]
) -> None:
    """
    Make an HTML report from metrics data for codefile/dir found on path.

    :param path: Path to measured file/dir
    :param output: Destination path
    :param data: List of data-tuples
    :param headers: Tuples of header-strings for the metrics table
    """
    report_path, report_output = _check_output(output)
    templates_dir = (Path(__file__).parents[1] / "templates").resolve()
    report_template = Template((templates_dir / "report_template.html").read_text())

    table_headers = "".join([f"<th>{header}</th>" for header in headers])
    table_content = ""
    for line in data[::-1]:
        table_content += "<tr>"
        for element in line:
            element = element.replace("[32m", "<span class='green-color'>")
            element = element.replace("[31m", "<span class='red-color'>")
            element = element.replace("[33m", "<span class='orange-color'>")
            element = element.replace("[0m", "</span>")
            table_content += f"<td>{element}</td>"
        table_content += "</tr>"

    report_template = report_template.safe_substitute(
        headers=table_headers, content=table_content
    )
    report_output.write_text(report_template)

    try:
        copytree(str(templates_dir / "css"), str(report_path / "css"))
    except FileExistsError:
        pass

    logger.info(f"wily report on {str(path)} was saved to {report_path}")


def _check_output(output: Path, file_ending: str = ".html") -> T.Tuple[Path, Path]:
    if output.is_file and output.suffix == file_ending:
        report_path = output.parents[0]
        report_output = output
    else:
        report_path = output
        report_output = output.joinpath("index" + file_ending)
    report_path.mkdir(exist_ok=True, parents=True)
    return report_path, report_output


def generate_json_report(
    path: Path, output: Path, data: T.List[T.Tuple[str]], headers: T.Tuple[str]
) -> None:
    """
    Make a JSON file of report of latest commit for codefile/dir found on path.

    :param path: Path to measured file/dir
    :param output: Destination path
    :param data: List of data-tuples
    :param headers: Tuples of names of metrics
    """
    report_path, report_output = _check_output(output, ".json")
    metric_data = dict(zip(headers, data[-1]))
    metric_data["location"] = str(path)
    report_json_string = dumps(dict(issues=[metric_data]))
    report_output.write_text(report_json_string)

    logger.info(f"wily report on {str(path)} was saved to {report_path}")


FORMAT_MAP = {
    ReportFormat.HTML: generate_html_report,
    ReportFormat.JSON: generate_json_report,
}

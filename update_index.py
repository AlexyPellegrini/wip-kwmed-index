import argparse
import json
import urllib.request
import requests
import re
from urllib.error import URLError, HTTPError
from pathlib import Path
from lxml import html
from dataclasses import dataclass

SHA256_REGEX = re.compile("^[a-fA-F0-9]{64}$")


@dataclass
class PackageInfo:
    """Package information gathered from GitHub API calls"""
    name: str # package full name, including python and plaform tags, and version
    url: str # direct "browser" link for the package, suitable for basic HTTP requests
    sha256: str # package content hash, to combine with url in href argument


def get_or_create_tree(filepath: Path) -> html.HtmlElement:
    """Parses an HTML file or creates a new base structure."""
    if filepath.exists():
        return html.parse(filepath.as_posix()).getroot()
    else:
        root = html.Element("html")
        html.etree.SubElement(root, "body")
        return root


def save_tree(root: html.HtmlElement, filepath: Path):
    """Saves the lxml tree back to a file with the proper HTML5 DOCTYPE."""
    html_bytes = html.tostring(
        root,
        doctype="<!DOCTYPE html>",
        encoding="utf-8",
        method="html"
    )
    filepath.write_bytes(html_bytes)


def update_main_index(project_name: str):
    """Ensures the root index.html exists and contains a link to the project."""
    root_index = Path("index.html")
    root_element = get_or_create_tree(root_index)
    body = root_element.find("body")

    # check if an anchor with this href already exists
    if not body.xpath(f".//a[@href='{project_name}/']"):
        html.etree.SubElement(body, "a", href=f"{project_name}/").text = project_name
        html.etree.SubElement(body, "br")
        save_tree(root_element, root_index)
        print(f"Added {project_name} to root index.html")


def update_project_index(project_name: str, wheels: list[PackageInfo]):
    """Ensures the project index.html exists and adds verified links."""
    project_dir = Path(project_name)
    project_dir.mkdir(exist_ok=True)
    project_index = project_dir / "index.html"

    root_element = get_or_create_tree(project_index)
    body = root_element.find("body")

    modified = False
    for wheel in wheels:
        # Check if wheel already exists by examining text content of existing <a> tags
        if not any(a.text == wheel.name for a in body.xpath(".//a")):
            html.etree.SubElement(body, "a", href=f"{wheel.url}#sha256={wheel.sha256}").text = wheel.name
            html.etree.SubElement(body, "br")
            modified = True
            print(f"Successfully added link for {wheel.name}")

    if modified:
        save_tree(root_element, project_index)


def check_url_alive(url: str):
    """Performs a HEAD request to check if a URL is reachable. Raises an error if unreachable."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as response:
            if not (200 <= response.status < 400):
                raise ValueError(f"Bad HTTP status code: {response.status}")
    except (HTTPError, URLError, ValueError) as e:
        raise RuntimeError(f"ERROR: URL is unreachable ({url}). Reason: {e}")


def process_release(descr: dict, package_name: str) -> list[PackageInfo]:
    packages: list[PackageInfo] = []
    for asset in descr["assets"]:
        # filter out files that do not package our package name, releases may have other assets
        name: str = asset["name"]
        if name.split('-')[0] != package_name or name.split('.')[-1] != "whl":
            continue

        url: str = asset["browser_download_url"]
        check_url_alive(url)

        sha256: str = asset["digest"].split(':')[1]
        if not SHA256_REGEX.match(sha256):
            raise ValueError(f"ERROR: {name} wheel hash, \"{sha256}\", is not a valid sha256 hash")

        packages.append(PackageInfo(name, url, sha256))

    return packages


def list_packages(owner: str, repo: str, tag: str, package_name: str) -> list[PackageInfo]:
    response = requests.get(
        url=f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}",
        headers={"Accept": "application/vnd.github+json"}
    )

    if response.status_code != 200:
        raise ValueError(
            f"ERROR: Request to GitHub REST API failed with code #{response.status_code}.\n"
            "Reason: {response.reason}\n"
            "Body:\n{response.text}"
        )

    return process_release(response.json(), package_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="The python project name, used to filter wheels from release page.")
    parser.add_argument("--organization", required=True, help="Github organization name. May be a username.")
    parser.add_argument("--repository", required=True, help="Repository name in given organization.")
    parser.add_argument("--tag", required=True, help="Release tag identifier.")
    args = parser.parse_args()

    with open("sources.txt", "r") as file:
        if not f"{args.organization}/{args.repository}" in file.readlines():
            print(f"ERROR: {args.organization}/{args.repository} is not allowed to push to this index.")
            exit(1)

    packages = list_packages(args.organization, args.repository, args.tag, args.name)

    update_project_index(args.name, packages)
    update_main_index(args.name)

if __name__ == "__main__":
    main()
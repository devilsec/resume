#!/usr/bin/env python3
"""
Generates LaTeX, Markdown, and HTML copies of my résumé.

More information is available in the README file.

"""
import copy
import glob
import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import time

import git
import jinja2
import tqdm
import yaml


with open("config.yaml") as config_file:
    CONFIG = yaml.load(config_file)


def load_yaml(filename):
    """
    Load a YAML file.

    Parameters
    ----------
    filename : str
        The name of the file to load

    Returns
    -------
    dict
        The contents of the file.

    """
    with open(filename) as file:
        return yaml.load(file)


def files_of_type(ext, directory="."):
    """
    Find all files of a given type.

    Parameters
    ----------
    ext : str
        The file extension.
    directory : Optional[str]
        The directory to use. Default is the current directory.

    Yields
    ------
    str
        The matching filenames.

    """
    yield from glob.iglob("{}/*{}".format(directory, ext))


def environment_setup():
    """
    Create the build and output directories if they don't exist.

    """
    os.makedirs(CONFIG["BUILD_DIR"], exist_ok=True)
    os.makedirs(posixpath.join(CONFIG["OUTPUT_DIR"], CONFIG["LETTERS_DIR"]),
                exist_ok=True)


def md5(filename):
    """
    Return the MD5 hash of a file.

    Parameters
    ----------
    filename : str
        The name of the file to check.

    Returns
    -------
    str
        The MD5 hash in hexadecimal.

    """
    with open(filename) as fin:
        return hashlib.md5(fin.read().encode()).hexdigest()


def hash_map(ext=".tex"):
    """
    Generate a dictionary of all files of a given type and their hashes.

    Parameters
    ----------
    ext : Optional[str]
        The extension to search for. Default is ".tex".

    Returns
    -------
    dict[str, str]
        A list of filenames with their md5 hash.

        **Dictionary format :** {filename: md5}

    """
    return {f: md5(f) for f in files_of_type(ext, CONFIG["BUILD_DIR"])}


class ResumeGenerator(object):
    """
    Generates résumés.

    Attributes
    ----------
    data : dict
        The contents of the main YAML file.
    starting_hashes : dict[str, str]
        A list of all tex files with their MD5 hash before running.

    """
    def __init__(self):
        self.data = load_yaml(posixpath.join(CONFIG["YAML_DIR"],
                                             CONFIG["YAML_MAIN"] + ".yaml"))
        self.starting_hashes = hash_map()

    def run(self, contexts):
        """
        Generate the résumé in various formats.

        Parameters
        ----------
        contexts : tuple[ContextRenderer]
            The renderers for the formats to use.

        """
        self.handle_publications()
        self.generate_resumes(contexts)

        if LATEX_CONTEXT in contexts:
            self.generate_cover_letters()
            self.compile_latex()

        self.copy_to_output_dir()

    def handle_publications(self):
        """
        Fill or remove the publication section, if available.

        """
        if not any("publications" in item for item in self.data["order"]):
            return

        if "publications" not in self.data:
            pubs = load_yaml(
                posixpath.join(CONFIG["YAML_DIR"],
                               CONFIG["YAML_PUBLICATIONS"] + ".yaml"))
            if pubs:
                self.data["publications"] = pubs
            else:
                for item in self.data["order"]:
                    if "publications" in item:
                        self.data["order"].remove(item)
                        break

    def process_resume(self, context, base=CONFIG["BASE_FILE_NAME"]):
        """
        Render and save a résumé.

        Parameters
        ----------
        context : ContextRenderer
            The renderer to use.
        base : str
            The root filename for the résumé. The user's name would be prepended
            to this.

        """
        rendered_resume = context.render_resume(self.data)
        context.write(rendered_resume, base=base)

    def generate_resumes(self, contexts):
        """
        Process the necessary résumés.

        Parameters
        ----------
        contexts : tuple[ContextRenderer]
            The renderers for the formats to use.

        """
        for context in tqdm.tqdm(contexts, leave=True, desc="Rendering résumé",
                                 unit="formats"):
            self.process_resume(context)

    def generate_cover_letters(self):
        """
        Generate cover letters for all companies in the business YAML file.

        """
        businesses = load_yaml(
            posixpath.join(CONFIG["YAML_DIR"],
                           CONFIG["YAML_BUSINESSES"] + ".yaml"))

        if not businesses:
            return

        for business in tqdm.tqdm(businesses, desc="Generating cover letters",
                                  unit="letter", leave=True):
            self.data["business"] = businesses[business]
            self.data["business"]["body"] = LATEX_CONTEXT._render_template(
                CONFIG["LETTER_FILE_NAME"], self.data
            )
            self.process_resume(LATEX_CONTEXT, base=business)

    def compile_latex(self):
        """
        Compile changed LaTeX files into PDF.

        """
        changed_files = [file
                         for file in files_of_type(".tex", CONFIG["BUILD_DIR"])
                         if ((file in self.starting_hashes
                              and md5(file) != self.starting_hashes[file])
                             or not os.path.exists(file.replace(".tex",
                                                                ".pdf")))]
        if not changed_files:
            return

        for file in tqdm.tqdm(changed_files, desc="Generating PDFs",
                              leave=True, unit="pdf"):
            subprocess.call("{} -output-dir={} {}".format(self.data["engine"],
                                                          CONFIG["BUILD_DIR"],
                                                          file).split())

    @staticmethod
    def copy_to_output_dir():
        """
        Copy compiled résumés from the build directory to the output directory.

        """
        for ext in CONFIG["OUTPUT_TYPES"]:
            for file in files_of_type(ext, CONFIG["BUILD_DIR"]):
                if os.path.basename(file).startswith("0_"):
                    shutil.copyfile(file,
                                    posixpath.join(CONFIG["OUTPUT_DIR"],
                                                   os.path.basename(file)[2:]))
                else:
                    shutil.copy(file, posixpath.join(CONFIG["OUTPUT_DIR"],
                                                     CONFIG["LETTERS_DIR"]))


class ContextRenderer(object):
    """
    Renders a given context.

    Parameters
    ----------
    context_name : str
        The name of the context, corresponding to one of the template
        subdirectories.
    filetype : str
        The file extension to use.
    jinja_options : dict
        Options for Jinja.
    replacements : dict[str, str]
        A list of replacements to perform in order to change LaTeX formatting to
        the corresponding code for the context.

    Attributes
    ----------
    base_template : str
        The root filename for the résumé. The user's name would be prepended to
        this.
    context_name : str
        The name of the context.
    filetype : str
        The file extension to use.
    jinja_env : dict
        The Jinja environment.
    known_section_types : list
        A list of known sections for the context.
    replacements : dict[str, str]
        A list of replacements to perform in order to change LaTeX formatting to
        the corresponding code for the context.

    """
    def __init__(self, *, context_name, filetype, jinja_options, replacements):
        self.base_template = CONFIG["BASE_FILE_NAME"]
        self.context_name = context_name

        self.filetype = filetype
        self.replacements = replacements

        context_templates_dir = posixpath.join(CONFIG["TEMPLATES_DIR"],
                                               context_name)

        jinja_options = jinja_options.copy()
        jinja_options["loader"] = jinja2.FileSystemLoader(
            searchpath=context_templates_dir
        )
        jinja_options["undefined"] = jinja2.StrictUndefined
        self.jinja_env = jinja2.Environment(**jinja_options)

        self.known_section_types = [os.path.splitext(os.path.basename(s))[0]
                                    for s in files_of_type(
                                        self.filetype,
                                        posixpath.join(context_templates_dir,
                                                       CONFIG["SECTIONS_DIR"]))]

    def _make_replacements(self, data):
        """
        Perform replacements in order to change LaTeX formatting to the
        corresponding code for the context.

        Parameters
        ----------
        data : dict
            The résumé data to render.

        Returns
        -------
        data : dict
            A copy of the data containing the replaced strings.

        """
        data = copy.copy(data)

        if isinstance(data, str):
            for o, r in self.replacements.items():
                data = re.sub(o, r, data)

        elif isinstance(data, dict):
            for k, v in data.items():
                data[k] = self._make_replacements(v)

        elif isinstance(data, list):
            for idx, item in enumerate(data):
                data[idx] = self._make_replacements(item)

        return data

    @staticmethod
    def _make_double_list(items):
        """
        Change a list to a double list.

        Parameters
        ----------
        items : Iterable
            A list of items to double.

        Returns
        -------
        double_list : list[dict]
            A double list which can be used by the LaTeX renderer.

            **Dictionary format :** {first: second}

        """
        double_list = [{"first": items[i * 2], "second": items[i * 2 + 1]}
                       for i in range(len(items) // 2)]
        if len(items) % 2:
            double_list.append({"first": items[-1]})
        return double_list

    def _render_template(self, template_name, data):
        """
        Render a template.

        Parameters
        ----------
        template_name : str
            The name of the template.
        data : dict
            The data to be rendered.

        Returns
        -------
        str
            The rendered template.

        """
        return self.jinja_env.get_template(template_name + self.filetype)\
                             .render(**data)

    def _render_section(self, section, data):
        """

        Parameters
        ----------
        section : list[str, bool, str|bool, list[str]|str|bool]
            Details about the section:

            - section tag (str)
            - whether to show a title (bool)
            - the title of the section (str, or False if not shown)
            - the type of section (False to use the section tag, or a string, or
                                   or a list of strings)

        data : dict
            The data to be rendered.

        Returns
        -------
        str
            The rendered section.

        """
        section_tag, show_title, section_title, section_type = section
        section_data = {"name": section_title} if show_title else {}
        section_data["items"] = data[section_tag]
        section_data["theme"] = data["theme"]

        section_type = self._find_section_type(section_tag, section_type)
        section_data["type"] = section_type

        if section_type == "double_items":
            section_data["items"] = self._make_double_list(
                section_data["items"])

        section_template_name = posixpath.join(CONFIG["SECTIONS_DIR"],
                                               section_type)

        rendered_section = self._render_template(section_template_name,
                                                 section_data)
        return rendered_section

    def _find_section_type(self, section_tag, section_type):
        """
        Determine a section's type.

        If the section type is unknown, the default type as defined in the
        config file is used.

        Parameters
        ----------
        section_tag : str
            The tag of the section.
        section_type : list[str]|str|bool
            The declared type of the section. Use False to use the section tag.

        Returns
        -------
        section_type : str
            The type of the section.

        """
        context_type_name = self.context_name + "type"
        if isinstance(section_type, list):
            for t in section_type:
                if t.startswith(context_type_name):
                    section_type = t
                    break
            else:
                section_type = section_type[0]

        if section_type and section_type.startswith(context_type_name):
            section_type = section_type.split("_", maxsplit=1)[1]
        if not section_type and section_tag in self.known_section_types:
            section_type = section_tag
        if section_type not in self.known_section_types:
            section_type = CONFIG["DEFAULT_SECTION"]

        return section_type

    def render_resume(self, data):
        """
        Render the entire résumé.

        Parameters
        ----------
        data : dict
            The data to render.

        Returns
        -------
        str
            The rendered résumé.

        """
        data = self._make_replacements(data)
        self.username = data["name"]["abbrev"]

        body = ""
        for section in tqdm.tqdm(data["order"], desc=self.context_name,
                                 unit="sections", nested=True):
            body += self._render_section(section, data).rstrip() + "\n\n\n"
        data["body"] = body

        if data["last_updated_method"] == "git":
            last_updated = time.localtime(git.Repo().head.commit.committed_date)
        elif data["last_updated_method"] == "time":
            last_updated = time.localtime(time.time())
        data["updated"] = time.strftime(CONFIG["DATE_FMT"], last_updated)

        return self._render_template(self.base_template, data).rstrip() + "\n"

    def write(self, output_data, base=CONFIG["BASE_FILE_NAME"]):
        """
        Save the résumé to file.

        Parameters
        ----------
        output_data : str
            The data to be written.
        base : str
            The root filename for the résumé. The user's name would be prepended
            to this.

        Notes
        -----
        If the base is the default name, then a "0_" is also prepended to
        visually separate the base résumé from potential business résumés.

        """
        if base == CONFIG["BASE_FILE_NAME"]:
            prefix = "0_"
        else:
            prefix = ""
        output_file = posixpath.join(CONFIG["BUILD_DIR"],
                                     "{prefix}{name}_{base}{ext}".format(
                                         prefix=prefix,
                                         name=self.username,
                                         base=base,
                                         ext=self.filetype)
                                     )
        with open(output_file, "w") as fout:
            fout.write(output_data)


LATEX_CONTEXT = ContextRenderer(
    context_name="latex",
    filetype=".tex",
    jinja_options=dict(
        block_start_string='~<',
        block_end_string='>~',
        variable_start_string='<<',
        variable_end_string='>>',
        comment_start_string='<#',
        comment_end_string='#>',
        trim_blocks=True,
        lstrip_blocks=True,
    ),
    replacements={}
)


MARKDOWN_CONTEXT = ContextRenderer(
    context_name='markdown',
    filetype='.md',
    jinja_options=dict(
        trim_blocks=True,
        lstrip_blocks=True
    ),
    replacements={
        r'\\ ': ' ',                      # spaces
        r'\\textbf{([^}]*)}': r'**\1**',  # bold text
        r'\\textit{([^}]*)}': r'*\1*',    # italic text
        r'\\LaTeX': 'LaTeX',              # \LaTeX
        r'\\TeX': 'TeX',                  # \TeX
        '---': '-',                       # em dash
        '--': '-',                        # en dash
        r'``([^\']*)\'\'': r'"\1"',       # quotes
    }
)


HTML_CONTEXT = ContextRenderer(
    context_name='html',
    filetype='.html',
    jinja_options=dict(
        trim_blocks=True,
        lstrip_blocks=True
    ),
    replacements={
        r'\\ ': '&nbsp;',                              # spaces
        r'\\textbf{([^}]*)}': r'<strong>\1</strong>',  # bold
        r'\\textit{([^}]*)}': r'<em>\1</em>',          # italic
        r'\\LaTeX': 'LaTeX',                           # \LaTeX
        r'\\TeX': 'TeX',                               # \TeX
        '---': '&mdash;',                              # em dash
        '--': '&ndash;',                               # en dash
        r'``([^\']*)\'\'': r'"\1"',                    # quotes
    }
)


def main():
    """
    Main hook for script.

    """
    environment_setup()
    ResumeGenerator().run(contexts=(HTML_CONTEXT,
                                    LATEX_CONTEXT,
                                    MARKDOWN_CONTEXT))


if __name__ == '__main__':
    main()

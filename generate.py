import os

import jinja2
import yaml


os.makedirs("build", exist_ok=True)


latex_renderer = jinja2.Environment(
    block_start_string='~<',
    block_end_string='>~',
    variable_start_string='<<',
    variable_end_string='>>',
    comment_start_string='<#',
    comment_end_string='#>',
    trim_blocks=True,
    lstrip_blocks=True,
    loader=jinja2.FileSystemLoader(os.path.abspath('.')),
)


def main():
    template = latex_renderer.get_template("templates/latex/resume.tex")

    with open("resume.yaml") as resume_data:
        data = yaml.safe_load(resume_data)
    file_root = data["name"]["abbrev"]

    with open("businesses.yaml") as businesses_data:
        businesses = yaml.safe_load(businesses_data)

    for business in businesses:
        with open("build/{}_{}.tex".format(file_root, business), "w") as resume:
            resume.write(template.render(**data, business=businesses[business]))

    with open("build/{}_resume.tex".format(file_root), "w") as resume:
        resume.write(template.render(**data))


if __name__ == '__main__':
    main()
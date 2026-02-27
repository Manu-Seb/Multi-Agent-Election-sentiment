from setuptools import find_packages, setup


setup(
    name="entity-graph-processor",
    version="0.1.0",
    description="AI analysis models and entity graph processor service",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "avro>=1.11.0",
        "pydantic>=2.0.0",
        "fastavro>=1.7.0",
        "confluent-kafka>=2.0.0",
    ],
)

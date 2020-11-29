pipeline {
    agent { label 'ol-build' }
    stages {
        stage('Python') {
            matrix {
                axes {
                    axis {
                        name 'PYTHON_VERSION'
                        values '2.7', '3.8'
                    }
                }
                // Note the python image contains both `make` and `git`
                agent { docker {
                    image "python:$PYTHON_VERSION"
                    label 'ol-build'
                } }
                stages {
                    stage('Echo') { steps { sh 'pwd; ls; git status' } }
                    stage('Install') { steps { sh 'pip install -r requirements_test.txt' } }
                    stage('Lint') { steps { sh 'make lint' } }
                    stage('Unit Test') { steps { sh 'make test-py' } }
                }
            }
        }
    }
}

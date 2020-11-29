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
                // Note the python image contains both `make` and `git` (which we need)
                agent { docker {
                    image "python:$PYTHON_VERSION"
                    label 'ol-build'
                } }
                stages {
                    stage('Use Infogami master') {
                        when { expression { PYTHON_VERSION ==~ /3.*/ } }
                        steps {
                            dir('vendor/infogami') {
                                sh 'git pull origin master'
                            }
                        }
                    }
                    stage('Install') { steps { sh 'pip install -r requirements_test.txt' } }
                    stage('Lint') { steps { sh 'make lint' } }
                    stage('Unit Test') { steps { sh 'make test-py' } }
                }
            }
        }
    }
}

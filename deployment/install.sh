#!/bin/bash
sysctl -w net.core.somaxconn=65535

BASEDIR=$(dirname "$0")
cd $BASEDIR


VERSION=1.0.0
PYTHON_PATH=/usr/bin/python2.7
TERRA_CONTAINER_EXPERIMENT_VENV_DIR=/usr/local/bin/terra_container_experiment_venv

PYPI_SOURCE=http://mirrors.aliyun.com/pypi/simple/


LOG_TO_FILE=y
LOG_FILE="./install.log"
if [ -f $LOG_FILE ]; then
    rm $LOG_FILE
fi
echo -n >$LOG_FILE

BOLD="\e[1m"
NORMAL="\e[0m"


#tput clear
#trap "kill 0" EXIT

log_n() {
    echo -en "${BOLD}$1${NORMAL}"
}


log() {
    echo -e "${BOLD}$1${NORMAL}"
}

error_exit() {
    log "ERROR: $1"
    log "Exit"
    exit 1
}

clear_inputs() {
    read -N 10000000 -t 0.01
}

env_check() {
    if [ $EUID != 0  ]; then
        sudo "$0" "$@"
        exit 0
    fi

    log_n "Checking environment ... "

    source /etc/lsb-release
    if [ "$DISTRIB_ID" != "Ubuntu" ]; then
        log "\nERROR: Only Ubuntu is supported."
        log "Exit"
        exit 1
    fi
    log "OK"
}

config() {

    ether_user=`grep ADMIN_USER /etc/profile`
    if [ -z "$ether_user" ]; then
        log "export ADMIN_USER=super" | tee -a /etc/profile > /dev/null
    fi

    ether_pass=`grep ADMIN_PASSWORD /etc/profile`
    if [ -z "$ether_pass" ]; then
        log "export ADMIN_PASSWORD=sdnlab" | tee -a /etc/profile > /dev/null
    fi

    source /etc/profile

}

install_virtualenv() {
    pip install virtualenv
}

check_installed() {
    if [ "$(command -v "$1")" ]; then
        return 1
     else
        return 0
     fi
} 

ensure_virtualenv() {
    
    check_installed virtualenv 
    result=$?
    if [ $result -eq 1 ]; then
        log "virtualenv is already installed!"
    else
        log "install virtualenv!"
        install_virtualenv
    fi
    
    if [ ! -d $TERRA_CONTAINER_EXPERIMENT_VENV_DIR ]; then
        mkdir -p $TERRA_CONTAINER_EXPERIMENT_VENV_DIR
        virtualenv -p $PYTHON_PATH $TERRA_CONTAINER_EXPERIMENT_VENV_DIR
        log "create virtual env"
    fi

    source $TERRA_CONTAINER_EXPERIMENT_VENV_DIR/bin/activate
    log "virtual env is activated!"
}

do_preparations() {

    apt install -y git  libgmp-dev openssl libssl-dev xvfb xserver-xephyr chromium-browser chromium-browser-l10n libxi6 libgconf-2-4
    
    #install progress: pip ===> virtualenv===> pbr=====> setuptools
    #install pip: in offline installation, we call put all the packages into pip_packages before
    mkdir -p ~/.pip
    cat > ~/.pip/pip.conf <<PIP_CFG
[global]
index-url=$PYPI_SOURCE
find-links=file://~/.cache/pip_packages
[install]
trusted-host=mirrors.aliyun.com
PIP_CFG
    mkdir -p ~/.cache/pip_packages
    #online install pip
    apt-get install python-pip python-dev build-essential -y
    #offline install pip
    #python get-pip.py

    #ensure virtualenv
    ensure_virtualenv
    
    #install pbr
    pip install pbr==1.10.0

    #install setuptools
    pip install setuptools==20.4

}


check_lib() {

    pip list | grep "$1"
    result=$?
    if [ $result -eq 0 ]; then
        return 1
     else
        return 0
     fi  

}

install_dependency() {

    check_lib $1
    result=$?
    if [ $result -eq 0 ]; then
        log "install $1" 
        log "git clone $1" 
        lib_dir=$(dirname "$PWD")"/lib/"
        if [ ! -d $lib_dir ]; then
            log $lib_dir" is not exist!"
            mkdir -p $lib_dir
        else
            log $lib_dir" is exist!"
        fi
        cd ../lib/
        if [ -d $1 ]; then
	    rm -r $1
        fi
        git clone https://gzq0727:gzq610583@github.com/gzq0727/$1.git

        cd $1/deployment/

        log "install $1  in $TERRA_CONTAINER_EXPERIMENT_VENV_DIR"
        ./install.sh $TERRA_CONTAINER_EXPERIMENT_VENV_DIR 

        log "return to terra_container_experiment/deployment"
        cd ../../../deployment/
    else
        log "$1 is already installed!"
    fi  
}

install_dependencies() {

    log "terra_container_experiment and terra are referenced,but we do not install terra here!" 

}


build() {

    cd ../ 
    if [ -d dist ]; then
        rm -r dist/
        log "clear dist directory"
    fi 
    root_dir=`pwd` 
    if [ -d "$root_dir" ]; then
         pushd ${root_dir}
         if [ -d ".git" ] && [ -f "setup.py" ]; then
              git_tag="git tag "${VERSION}
              eval $git_tag
              eval "python setup.py sdist"
         fi
         popd
    fi  
    wait
    cd deployment/

}

install() {

    pip uninstall -yq container_expt
    pip install ../dist/container_expt-${VERSION}.tar.gz
    if [ $? != 0 ]; then
        error_exit "install terra_container_experiment failed!"
    fi

}

run_services() {
   
    
    log "terra_container_experiment is running in virtual env "$TERRA_CONTAINER_EXPERIMENT_VENV_DIR
    #deactivate

}

terra_container_experiment_install() {

    #set the virtualenv path
    if [ -n "$1" ]; then
        TERRA_CONTAINER_EXPERIMENT_VENV_DIR=$1
    fi
    log "TERRA_CONTAINER_EXPERIMENT_VENV_DIR = "$TERRA_CONTAINER_EXPERIMENT_VENV_DIR

    #ensure the operation system is ubuntu
    env_check
 
 
    #make preparations:check mysql/virtualenv/supervisor
    do_preparations
    log "do preparations done!"


    #install dependencies:terra
    install_dependencies
    log "install terra_container_experiment dependencies done!"

    #configure terra_container_experiment ADMIN_USER  and ADMIN_PASSWORD in /etc/profile
    config
    log "config terra_container_experiment done"

    #build terra_container_experiment source
    build
    log "build terra_container_experiment done!"


    #install terra_container_experiment
    install
    log "install terra_container_experiment done!"

    #run terra_container_experiment service
    run_services
    log "run terra_container_experiment service done!"
}

if [ "$LOG_TO_FILE" == "y" ]; then
     # Redirect the std ouput both to screen and log file
     exec 4<&1 5<&2 1>&2>&>(tee -a >(sed -ur 's/\x1B\[([0-9]{1,2}(;[0-9]{1,2})?)?[m|K]//g' > $LOG_FILE))
fi
terra_container_experiment_install $1

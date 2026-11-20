#!/usr/bin/env bash
set -euo pipefail
course=/mnt/home/public/course-envs/CSIT6000V/assignment1
environments=$(tcloud env ls 2>&1)
[[ "$environments" == *'conda environments:'* && "$environments" != *'[Tcloud Error]'* ]]
listing=$(tcloud ls -a -l ../ 2>&1)
[[ "$listing" != *'[Tcloud Error]'* ]]
if grep -Eq '(^|[[:space:]])\.condarc($|[[:space:]])' <<< "$listing"; then
    packages=$(tcloud env ls -n mpi-gemm-v1 2>&1)
    if [[ "$packages" == *"$course/mpi-gemm-v1:"* && "$packages" != *'[Tcloud Error]'* ]]; then
        echo 'Course bootstrap is configured.'
        exit 0
    fi
    echo 'Existing Conda configuration needs teaching-team review.' >&2
    exit 1
fi
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$stage/envs/mpi-gemm-v1"
printf 'envs_dirs:\n  - %s\n' "$course" > "$stage/.condarc"
tcloud upload "$stage/envs/mpi-gemm-v1" ../.Miniconda3/envs/ | tr -d '\000'
tcloud upload "$stage/.condarc" ../ | tr -d '\000'
packages=$(tcloud env ls -n mpi-gemm-v1 2>&1)
[[ "$packages" == *"$course/mpi-gemm-v1:"* && "$packages" != *'[Tcloud Error]'* ]]
echo 'Course bootstrap is configured.'

FROM quay.io/pypa/manylinux_2_28_x86_64 AS glibc

# Preserve the manylinux Python stack, replacing only the C runtime for acceptance.
RUN curl --fail --location --proto '=https' --tlsv1.2 \
        https://ftp.gnu.org/gnu/glibc/glibc-2.30.tar.xz -o /tmp/glibc.tar.xz \
    && echo 'e2c4114e569afbe7edbc29131a43be833850ab9a459d81beb2588016d2bbb8af  /tmp/glibc.tar.xz' | sha256sum --check \
    && tar -C /tmp -xf /tmp/glibc.tar.xz \
    && mkdir /tmp/glibc-build \
    && cd /tmp/glibc-build \
    && /tmp/glibc-2.30/configure --prefix=/usr --libdir=/usr/lib64 --disable-werror \
    && make -j2 \
    && make install DESTDIR=/tmp/glibc-root

FROM quay.io/pypa/manylinux_2_28_x86_64
COPY --from=glibc /tmp/glibc-root/lib64/ /lib64/
RUN test "$(getconf GNU_LIBC_VERSION)" = 'glibc 2.30' \
    && /opt/python/cp312-cp312/bin/python -c \
        'import platform; assert platform.libc_ver() == ("glibc", "2.30")'

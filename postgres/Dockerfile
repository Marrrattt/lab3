FROM postgres:18-bookworm
RUN localedef -i ru_RU -c -f UTF-8 -A /usr/share/locale/locale.alias ru_RU.UTF-8
ENV LANG=ru_RU.UTF-8
COPY sql/ /docker-entrypoint-initdb.d/
CMD ["postgres", "-c", "password_encryption=scram-sha-256"]

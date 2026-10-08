FROM python:3.12-slim
ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections
RUN pip install --no-cache-dir "ansible-core>=2.18" "kubernetes>=30" PyYAML \
 && ansible-galaxy collection install kubernetes.core -p /usr/share/ansible/collections \
 && useradd --create-home ansible
USER ansible
WORKDIR /work

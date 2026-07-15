import subprocess
import shutil


class DockerExecError(Exception):
    pass


class DockerNotAvailableError(Exception):
    pass


class DockerOps:

    @staticmethod
    def is_available():
        return shutil.which("docker") is not None

    @staticmethod
    def _run(cmd, timeout=120, input_data=None):
        try:
            kwargs = {"capture_output": True, "timeout": timeout}
            if input_data is not None:
                kwargs["input"] = input_data
                if isinstance(input_data, str):
                    kwargs["text"] = True
                else:
                    kwargs["text"] = False
            else:
                kwargs["text"] = True
            proc = subprocess.run(cmd, **kwargs)
            return proc
        except FileNotFoundError:
            raise DockerNotAvailableError(
                "Perintah docker tidak ditemukan. Pastikan Docker sudah terinstall."
            )
        except subprocess.TimeoutExpired:
            raise DockerExecError("Perintah docker timeout setelah {} detik.".format(timeout))

    @staticmethod
    def list_sql_containers():
        proc = DockerOps._run([
            "docker", "ps",
            "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}",
            "--filter", "status=running"
        ])
        containers = []
        if proc.returncode != 0:
            return containers
        for line in proc.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[0]
                image = parts[1]
                status = parts[2] if len(parts) > 2 else "running"
                containers.append({
                    "name": name,
                    "image": image,
                    "status": status
                })
        return containers

    @staticmethod
    def exec_command(container, cmd, timeout=120, user=None, env=None, input_data=None):
        docker_cmd = ["docker", "exec"]
        if env:
            for key, val in env.items():
                docker_cmd.extend(["-e", "{}={}".format(key, val)])
        if user:
            docker_cmd.extend(["--user", str(user)])
        if input_data is not None:
            docker_cmd.append("-i")
        docker_cmd.append(container)
        docker_cmd.extend(cmd)
        proc = DockerOps._run(docker_cmd, timeout=timeout, input_data=input_data)
        if proc.returncode != 0:
            raise DockerExecError(
                "Gagal menjalankan perintah di container '{}':\n{}".format(
                    container, proc.stderr.strip() or proc.stdout.strip()
                )
            )
        return proc.stdout.strip()


    @staticmethod
    def copy_to_container(container, src, dst):
        proc = DockerOps._run(["docker", "cp", src, "{}:{}".format(container, dst)])
        if proc.returncode != 0:
            raise DockerExecError(
                "Gagal menyalin file ke container '{}':\n{}".format(
                    container, proc.stderr.strip()
                )
            )

    @staticmethod
    def copy_from_container(container, src, dst):
        proc = DockerOps._run(["docker", "cp", "{}:{}".format(container, src), dst])
        if proc.returncode != 0:
            raise DockerExecError(
                "Gagal menyalin file dari container '{}':\n{}".format(
                    container, proc.stderr.strip()
                )
            )

    @staticmethod
    def check_file_exists(container, path):
        proc = DockerOps._run(["docker", "exec", container, "test", "-f", path])
        return proc.returncode == 0

    @staticmethod
    def mkdir(container, path):
        proc = DockerOps._run(["docker", "exec", container, "mkdir", "-p", path])
        if proc.returncode != 0:
            raise DockerExecError(
                "Gagal membuat direktori '{}' di container '{}'".format(path, container)
            )

    @staticmethod
    def remove_file(container, path):
        DockerOps._run(["docker", "exec", container, "rm", "-f", path])

    @staticmethod
    def write_text_file(container, content, path):
        if isinstance(content, str):
            content = content.encode("utf-8")
        DockerOps.exec_command(container, ["bash", "-c", "cat > '{}'".format(path)],
            input_data=content)

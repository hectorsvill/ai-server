import docker
import sys

def purge_all_containers():
    try:
        # Initialize Docker client
        client = docker.from_env()
        
        # Get list of all containers (including stopped ones)
        containers = client.containers.list(all=True)
        
        if not containers:
            print("No containers found.")
            return
        
        print(f"Found {len(containers)} containers to remove.")
        
        # Remove each container
        for container in containers:
            try:
                print(f"Removing container: {container.name} (ID: {container.id})")
                # Remove container with force=True to kill running containers and remove volumes
                container.remove(force=True, v=True)
                print(f"Successfully removed container: {container.name}")
            except docker.errors.APIError as e:
                print(f"Error removing container {container.name}: {e}")
        
        print("All containers have been removed and purged.")
        
    except docker.errors.DockerException as e:
        print(f"Error connecting to Docker: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Confirm before proceeding
    confirmation = input("Are you sure you want to delete ALL Docker containers and their volumes? (yes/no): ")
    if confirmation.lower() == 'yes':
        purge_all_containers()
    else:
        print("Operation cancelled.")


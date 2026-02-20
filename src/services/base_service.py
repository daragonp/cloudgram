# src/services/base_service.py
from abc import ABC, abstractmethod

class CloudService(ABC):
    @abstractmethod
    async def upload(self, local_path, file_name):
        """Debe retornar el enlace público o de visualización del archivo"""
        pass

    @abstractmethod
    async def list_files(self, path="/"):
        pass
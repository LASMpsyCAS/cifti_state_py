clear;clc;
Lgii_filename = '/data4/workingFolder/wuguowei/Language_multi/code/10k/S900.L.inflated_MSMAll.10k_fs_LR.surf.gii';
Rgii_filename = '/data4/workingFolder/wuguowei/Language_multi/code/10k/S900.R.inflated_MSMAll.10k_fs_LR.surf.gii';
Lgii = gifti(Lgii_filename);
fsLR_Firstadjacent_vertex_lh_10k = calculate_surface_neighbors(Lgii.vertices, Lgii.faces);
Rgii = gifti(Rgii_filename);
fsLR_Firstadjacent_vertex_rh_10k = calculate_surface_neighbors(Rgii.vertices, Rgii.faces);
save('/data4/workingFolder/wuguowei/Language_multi/Single_parcellation_project/code/atlas/fsLR_Firstadjacent_vertex_10k.mat','fsLR_Firstadjacent_vertex_lh_10k','fsLR_Firstadjacent_vertex_rh_10k')

function neighbors = calculate_surface_neighbors(vertices, faces)
    % 初始化邻居列表
    num_vertices = size(vertices, 1);
    neighbors = cell(num_vertices, 1);
    
    % 遍历所有面
    for i = 1:size(faces, 1)
        % 获取当前面的三个顶点
        v1 = faces(i, 1);
        v2 = faces(i, 2);
        v3 = faces(i, 3);
        
        % 更新每个顶点的邻居列表
        neighbors{v1} = unique([neighbors{v1}, v2, v3]);
        neighbors{v2} = unique([neighbors{v2}, v1, v3]);
        neighbors{v3} = unique([neighbors{v3}, v1, v2]);
    end
    
    % 确保每个顶点不包含自己作为邻居
    for i = 1:num_vertices
        neighbors{i} =[i-1 neighbors{i}-1];
    end
end